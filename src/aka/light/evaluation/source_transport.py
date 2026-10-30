"""Native Eval archives and Runtime-owned whole-tree diagnostics.

Logical task identity contains exact sources, not expiring OSS URLs. Persist the
uploaded wire request before POST so an uncertain submission reuses that same
reservation and idempotency key on recovery.
"""
from __future__ import annotations
import gzip
import hashlib
import http.client
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import tarfile
import tempfile
import time
import urllib.parse
import zipfile

from aka.light.workspace.sources import (
    entrypoint, snapshot, unpack, relative, requires_archive,
)

ARCHIVES = "__atrex_eval_archives"
DIAGNOSTIC = "__atrex_source_diagnostic"
MAX_INLINE_DEV_BYTES = 120 * 1024
PROFILES = {
    "pre": "https://pre-atrex-gateway.alibaba-inc.com",
    "prod": "https://atrex-gateway.alibaba-inc.com",
}
TERMINAL_JOB_STATES = {"succeeded", "failed", "cancelled", "canceled"}


class SourceUploadError(RuntimeError):
    """Archive preparation failed before the GPU job submission began."""

    def __init__(self, status=None):
        self.status = status
        super().__init__("Source archive upload failed" + (f" (HTTP {status})" if status else " (transport)"))


def attach(request, workspace, *, side="candidate", source=None):
    policy, files = unpack(source if source is not None else snapshot(workspace))
    # Legacy raw kernel.py snapshots retain the native inline transport. Every
    # managed adapter tree carries its implementation and therefore uses the
    # exact source-archive contract.
    if not requires_archive(policy, files):
        return request
    if policy["package_root"] != ".":
        raise ValueError("Native Eval archives require package_root='.'; no Dev fallback")
    archive = f"archives/{side}.tar.gz"
    request.setdefault(ARCHIVES, {})[archive] = {k: v.decode() for k, v in files.items()}
    descriptor = {"archive": archive, "entry_point": entrypoint(policy)}
    if side == "candidate":
        request["candidate"] = descriptor
    else:
        request["abba"]["baseline"] = descriptor
    requirements = list(request.get("requirements", []))
    for item in policy["runtime_requirements"]:
        spec = item["distribution"] + item.get("version", "")
        if spec not in requirements:
            requirements.append(spec)
    if requirements:
        request["requirements"] = requirements
    return request


def archive_bytes(files):
    output = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=output, mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as tar:
            for name, text in sorted(files.items()):
                relative(name)
                data = text.encode()
                member = tarfile.TarInfo(name)
                member.size, member.mode = len(data), 0o644
                tar.addfile(member, io.BytesIO(data))
    return output.getvalue()


def _dev_archive(request):
    """Use Runtime's verified bootstrap for oversized Dev source payloads."""
    from aka.light.evaluation import oss_remote
    command = request.get("command")
    if not isinstance(command, str) or not command.strip():
        raise ValueError("OSS Dev transport requires a nonempty command")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as bundle:
        for name, text in sorted(request["files"].items()):
            oss_remote.validate_path(name)
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system, info.external_attr = 3, 0o100644 << 16
            bundle.writestr(info, text.encode(), zipfile.ZIP_DEFLATED, 9)
    data = output.getvalue()
    digest = hashlib.sha256(data).hexdigest()
    archive, driver = f"__atrex_oss_payload_{digest}.zip", f"__atrex_oss_unpack_{digest}.py"
    occupied = set(request["files"]) | {v["path"] for v in request.get("oss_files", [])}
    if archive in occupied or driver in occupied:
        raise ValueError("OSS transport staging paths conflict with request files")
    wire = dict(request, files={driver: Path(oss_remote.__file__).read_text()},
                command=f"python3 {shlex.quote(driver)} {shlex.quote(archive)} {digest} && (\n{command}\n)")
    return {archive: data}, wire


def uploaded_request(request, wire_key, *, json_call, upload, kind="eval"):
    from aka.light.experiment.store import JOB_ROOT_ENV, read_json, private_write
    files = request.get("files")
    large_dev = (kind == "dev" and isinstance(files, dict)
                 and all(isinstance(k, str) and isinstance(v, str) for k, v in files.items())
                 and sum(len(v.encode()) for v in files.values()) > MAX_INLINE_DEV_BYTES)
    if ARCHIVES not in request and not large_dev:
        return dict(request, idempotency_key=wire_key)
    key = hashlib.sha256(wire_key.encode()).hexdigest()
    root = os.environ.get(JOB_ROOT_ENV)
    path = Path(root) / "uploads" / (key + ".json") if root else None
    logical = hashlib.sha256(json.dumps([kind, request], sort_keys=True).encode()).hexdigest()
    if path is not None:
        try:
            saved = read_json(path)
        except FileNotFoundError:
            pass
        else:
            if saved["logical"] != logical:
                raise ValueError("Persisted Gateway upload belongs to a different source request")
            return saved["wire"]
    if large_dev:
        packed, wire = _dev_archive(request)
    else:
        if kind != "eval" or request.get("oss_files"):
            raise ValueError("Native Eval archives cannot mix existing upload references or Dev tasks")
        packed = {name: archive_bytes(files) for name, files in sorted(request[ARCHIVES].items())}
        wire = {k: v for k, v in request.items() if k != ARCHIVES}
    descriptors = [{"path": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                   for name, data in packed.items()]
    reservation = json_call("POST", "/v1/uploads", {"kind": kind, "spec": request["spec"], "files": descriptors}, 30)
    uploads = reservation.get("uploads")
    if not reservation.get("job_id") or not isinstance(uploads, list) or len(uploads) != len(packed):
        raise ValueError("Source upload reservation is incomplete")
    for descriptor, item in zip(descriptors, uploads, strict=True):
        if item.get("path") != descriptor["path"] or not item.get("put_url") or not item.get("upload_ref"):
            raise ValueError("Source upload reservation identity mismatch")
    for item in uploads:
        upload(item["put_url"], packed[item["path"]])
    wire["oss_files"] = [*wire.get("oss_files", []),
                         *({"path": v["path"], "upload_ref": v["upload_ref"]} for v in uploads)]
    wire["idempotency_key"] = "oss:" + hashlib.sha256(json.dumps([wire_key, wire], sort_keys=True).encode()).hexdigest()
    if path is not None:
        private_write(path, {"logical": logical, "wire": wire})
    return wire


def upload_bytes(url, data):
    # Match Agate's presigned PUT contract. urllib adds an unsigned Content-Type
    # to byte bodies, which makes OSS reject the signature. Never send Gateway
    # authorization or follow redirects with the storage credential.
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        raise ValueError("Invalid source archive upload URL")
    connection = (http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection)(
        parts.hostname, parts.port, timeout=120)
    try:
        target = parts.path + ("?" + parts.query if parts.query else "")
        connection.request("PUT", target, body=data, headers={"Content-Length": str(len(data))})
        response = connection.getresponse()
        response.read(4096)
        if not 200 <= response.status < 300:
            raise SourceUploadError(response.status)
    except (OSError, http.client.HTTPException):
        # Exception strings may include the temporary credential-bearing URL.
        raise SourceUploadError() from None
    finally:
        connection.close()


def _agate_configuration(environment=None):
    """Read the same bounded operator configuration as the Agate CLI.

    Source collection belongs to Light, but endpoint selection, authentication,
    upload and job lifecycle belong to the published SDK.  Keep secrets only in
    the SDK auth provider; they never enter a request, checkpoint or child argv.
    """
    from aka.light.agent.session_tail import read_regular_bytes
    environment = os.environ if environment is None else environment
    configured_home = environment.get("HOME")
    home = Path(configured_home) if configured_home else Path.home()
    try:
        value = json.loads(read_regular_bytes(
            home / ".atrex/config.json", limit=64 * 1024
        ))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, UnicodeError) as error:
        from aka.light.runtime.request_errors import GatewayConfigurationError
        raise GatewayConfigurationError(
            "Supervisor Agate config is unreadable or invalid.",
            next_action=(
                "Ask the operator to repair ~/.atrex/config.json; do not change "
                "credentials or bypass the Runtime."
            ),
        ) from error
    if not isinstance(value, dict):
        from aka.light.runtime.request_errors import GatewayConfigurationError
        raise GatewayConfigurationError("Supervisor Agate config must be a JSON object.")
    return value


def _load_sdk_client(
    args,
    environment=None,
    *,
    authority_url=None,
    authority_profile=None,
):
    """Construct the public Agate SDK client using CLI-compatible precedence."""
    try:
        from atrex_gateway_client import AkSkAuth, Client, NoAuth, TokenAuth
        from atrex_gateway_client.errors import GatewayError, TransportError
    except ModuleNotFoundError as error:
        from aka.light.runtime.request_errors import GatewayConfigurationError
        raise GatewayConfigurationError(
            "atrex-gateway-client>=0.14.22 is required for Gateway operations.",
            next_action=(
                "Ask the operator to install the declared Atrex AKA Light dependencies "
                "and restart the Supervisor; do not change Agent arguments or fall back to Dev."
            ),
        ) from error

    environment = os.environ if environment is None else environment
    config = _agate_configuration(environment)
    url = (
        authority_url
        or getattr(args, "url", None)
        or PROFILES.get(authority_profile or getattr(args, "gateway_profile", None))
        or environment.get("AGATE_URL")
        or config.get("url")
        or PROFILES["prod"]
    )
    parts = urllib.parse.urlsplit(url) if isinstance(url, str) else None
    if (
        parts is None
        or parts.scheme not in {"http", "https"}
        or not parts.hostname
        or parts.username
        or parts.password
    ):
        from aka.light.runtime.request_errors import GatewayConfigurationError
        raise GatewayConfigurationError("Agate base URL must be an HTTP(S) origin.")

    token = environment.get("AGATE_TOKEN") or config.get("token")
    access_key = environment.get("AGATE_AK") or config.get("ak")
    secret_key = environment.get("AGATE_SK") or config.get("sk")
    if any(value is not None and not isinstance(value, str)
           for value in (token, access_key, secret_key)):
        from aka.light.runtime.request_errors import GatewayConfigurationError
        raise GatewayConfigurationError("Agate credential fields must be strings.")
    if token:
        auth = TokenAuth(token)
    elif access_key and secret_key:
        auth = AkSkAuth(access_key, secret_key)
    elif access_key or secret_key:
        from aka.light.runtime.request_errors import GatewayConfigurationError
        raise GatewayConfigurationError(
            "Agate access key and secret key must be configured together."
        )
    else:
        auth = NoAuth()

    class LightAgateClient(Client):
        @staticmethod
        def _overload_delay(error, attempt=0):
            # Light owns bounded, durable retry state.  Surface overload instead
            # of sleeping inside an unobservable SDK submission call.
            return None

    client = LightAgateClient(
        url.rstrip("/"),
        auth=auth,
        timeout=min(600, max(1, int(getattr(args, "timeout", 600)))),
        transport_retries=0,
    )
    return client, url.rstrip("/"), GatewayError, TransportError


def validate_gateway_sdk(
    args,
    environment,
    *,
    authority_url=None,
    authority_profile=None,
):
    """Fail before staging when a Gateway request cannot construct its SDK client."""
    _load_sdk_client(
        args,
        environment,
        authority_url=authority_url,
        authority_profile=authority_profile,
    )


# Compatibility for integrations importing the narrower name from the first
# SDK-only Eval release.
validate_eval_sdk = validate_gateway_sdk


def _sdk_upload(client, url, data):
    """Give the SDK a private regular file without exposing its signed URL."""
    path = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix="atrex-light-source-", suffix=".archive", delete=False
        ) as output:
            output.write(data)
            path = output.name
        client.upload_file(url, path)
    finally:
        if path is not None:
            try:
                Path(path).unlink()
            except FileNotFoundError:
                pass


def _sdk_uploaded_request(client, request, wire_key, *, kind="eval"):
    """Prepare source archives through SDK methods and retain the exact wire."""
    def reserve(method, path, body, timeout):
        if method != "POST" or path != "/v1/uploads":
            raise ValueError("Unexpected Agate SDK upload reservation request")
        spec = body.get("spec") if isinstance(body, dict) else None
        targets = spec.get("target_hardware") if isinstance(spec, dict) else None
        if (
            not isinstance(targets, list)
            or len(targets) != 1
            or not isinstance(targets[0], str)
        ):
            raise ValueError("Agate SDK upload requires exactly one target hardware")
        num_gpus = spec.get("num_gpus", 1)
        return client.prepare_uploads(
            targets[0],
            body["files"],
            kind=body["kind"],
            num_gpus=num_gpus,
            placement=request.get("placement"),
        )

    return uploaded_request(
        request,
        wire_key,
        kind=kind,
        json_call=reserve,
        upload=lambda url, data: _sdk_upload(client, url, data),
    )


def _sdk_error_process(error, *, stage):
    """Preserve the SDK's safe structured error without leaking credentials."""
    payload = getattr(error, "payload", None)
    if not isinstance(payload, dict):
        payload = {
            "reason": "transport_failed",
            "error_class": "infra",
            "message": "Agate SDK transport failed",
        }
    status = getattr(error, "status", None)
    envelope = {"status": "failed", "error": payload}
    if isinstance(status, int):
        envelope["http_status"] = status
    stderr = f"agate-sdk: {stage} failed"
    if isinstance(status, int):
        stderr += f" (HTTP {status})"
    return subprocess.CompletedProcess(
        ["agate-sdk", stage], 1, json.dumps(envelope), stderr
    )


def run_sdk_job(
    gateway,
    args,
    request,
    sdk_kind,
    queue_wait_grace,
    *,
    identity_extra=None,
    eval_semantics=False,
):
    """Submit and collect one Gateway job through the published SDK.

    Light retains durable idempotency, retry classification and recovery state;
    the SDK owns endpoint/authentication resolution, uploads, submission, polling
    and cancellation for every public Gateway job kind.
    """
    from aka.light.execution.gateway_jobs import (
        AcceptedJobMissing,
        SubmissionRejected,
        SubmissionUncertain,
        annotate_deadline_timeout,
        configured_max_deadline_retries,
        configured_max_infra_retries,
        eval_retry_kind,
        execute_job,
        payload_identity,
        private_value_fingerprint,
        retry_kind,
        retry_delay,
    )
    from aka.light.experiment.store import EvidenceUnavailable

    client, url, GatewayError, TransportError = _load_sdk_client(args)
    prepared_wires = {}

    def submit(wire_key):
        try:
            wire = prepared_wires.get(wire_key)
            if wire is None:
                wire = _sdk_uploaded_request(
                    client, request, wire_key, kind=sdk_kind
                )
                prepared_wires[wire_key] = wire
        except GatewayError as error:
            process = _sdk_error_process(error, stage="upload")
            status = getattr(error, "status", None)
            retryable = (
                status is None
                or status in {408, 429}
                or (isinstance(status, int) and status >= 500)
            )
            raise SubmissionRejected(
                process, 503 if retryable else status
            ) from None
        except (OSError, ValueError) as error:
            process = subprocess.CompletedProcess(
                ["agate-sdk", "upload"],
                1,
                "",
                f"source preparation failed: {error}",
            )
            raise SubmissionRejected(process, 400) from None

        try:
            accepted = client.submit_job(sdk_kind, wire)
        except TransportError as error:
            raise SubmissionUncertain(
                _sdk_error_process(error, stage="submit")
            ) from None
        except GatewayError as error:
            process = _sdk_error_process(error, stage="submit")
            status = getattr(error, "status", None)
            # A complete 4xx/429/503 response proves no new job was accepted.
            # Other 5xx responses can come from a proxy after dispatch.
            if isinstance(status, int) and (status < 500 or status == 503):
                raise SubmissionRejected(process, status) from None
            raise SubmissionUncertain(process) from None
        job_id = accepted.get("job_id") if isinstance(accepted, dict) else None
        if not isinstance(job_id, str) or not job_id:
            raise SubmissionUncertain(subprocess.CompletedProcess(
                ["agate-sdk", "submit"],
                75,
                "",
                "Agate SDK returned no confirmed job ID",
            ))
        return subprocess.CompletedProcess(
            ["agate-sdk", "submit"], 0, json.dumps(accepted), ""
        )

    def poll(initial, remaining, *, recovery_probe=False):
        accepted = json.loads(initial.stdout)
        job_id = accepted["job_id"]
        deadline = time.monotonic() + remaining
        failures = 0
        job = accepted
        try:
            while True:
                if job.get("status") in TERMINAL_JOB_STATES:
                    return subprocess.CompletedProcess(
                        ["agate-sdk", "get", job_id],
                        0 if job.get("status") == "succeeded" else 1,
                        json.dumps(job),
                        "",
                    )
                available = deadline - time.monotonic()
                if available <= 0:
                    raise EvidenceUnavailable(
                        "Gateway collection deadline reached; the accepted Job is saved for reconciliation"
                    )
                try:
                    job = client.get_job(
                        job_id,
                        wait=not recovery_probe,
                        timeout=(
                            min(30, max(1, available))
                            if recovery_probe
                            else min(300, max(1, available))
                        ),
                    )
                    failures = 0
                except GatewayError as error:
                    if getattr(error, "status", None) == 404:
                        raise AcceptedJobMissing(job_id) from None
                    failures += 1
                    delay = retry_delay(failures)
                    if deadline - time.monotonic() <= delay:
                        raise EvidenceUnavailable(
                            "Saved Gateway Job could not be read before the collection deadline"
                        ) from None
                    time.sleep(delay)
                    continue
                if recovery_probe:
                    return subprocess.CompletedProcess(
                        ["agate-sdk", "get", job_id],
                        0 if job.get("status") == "succeeded" else 1,
                        json.dumps(job),
                        "",
                    )
        except (KeyboardInterrupt, SystemExit):
            try:
                client.cancel_job(job_id)
            except (GatewayError, OSError, ValueError):
                pass
            raise

    identity = {
        "url_fingerprint": private_value_fingerprint(url),
        "kind": sdk_kind,
        "payload": payload_identity(request),
    }
    if identity_extra:
        identity["execution_context"] = identity_extra
    process = execute_job(
        identity,
        submit,
        poll,
        wait_budget=args.timeout + queue_wait_grace,
        retry_classifier=eval_retry_kind if eval_semantics else retry_kind,
        max_infra_retries=configured_max_infra_retries(),
        max_deadline_retries=configured_max_deadline_retries(
            terminal_evaluation=eval_semantics,
        ),
        idempotent_submission=True,
        max_device_retries=10 if eval_semantics else None,
        device_retry_delay=60 if eval_semantics else None,
    )
    timeout_s = request.get("timeout_s")
    if timeout_s is None and isinstance(request.get("options"), dict):
        timeout_s = request["options"].get("timeout_s")
    return annotate_deadline_timeout(process, timeout_s)


def run_eval_sdk(
    gateway,
    args,
    request,
    queue_wait_grace,
    *,
    identity_extra=None,
):
    """Submit inline and source-tree Eval/ABBA through one SDK lifecycle."""
    return run_sdk_job(
        gateway,
        args,
        request,
        "eval",
        queue_wait_grace,
        identity_extra=identity_extra,
        eval_semantics=True,
    )

def diagnostic_request(request, workspace, kind, *, timeout=600):
    policy, files = unpack(snapshot(workspace))
    if not requires_archive(policy, files):
        return request
    if request.get("profiler", "ncu") != "ncu" or request.get("fmt") == "isa":
        raise ValueError("Source-tree diagnostics currently support NVIDIA NCU and auto/sass/ptx")
    reference = request.get("reference", request.get("diagnostic_reference"))
    parameters = {key: request[key] for key in ("profiler", "level", "counters", "kernel_regex", "kernel_name",
                  "source", "launch_skip", "launch_count", "top_kernels", "arch", "sanitize", "fmt") if key in request}
    timeout = min(10800, timeout + 30)
    diagnostic = {"operation": kind, "entrypoint": entrypoint(policy), "package_root": policy["package_root"],
                  "runtime_requirements": policy["runtime_requirements"], "requirements": request.get("requirements", []),
                  "shapes": reference["shapes"], "parameters": parameters, "lock_clocks": True,
                  "timeout_s": max(1, timeout - 30)}
    from aka.legacy.evaluation.resources import ncu_profile_script
    payload = {"spec": request["spec"], "command": "python3 __atrex_diagnostic.py request.json",
        "files": {**{"candidate/" + k: v.decode() for k, v in files.items()},
            "input.py": reference["input_py"], "request.json": json.dumps(diagnostic),
            "__atrex_diagnostic.py": Path(__file__).with_name("source_diagnostics.py").read_text(),
            "ncu_profile.py": ncu_profile_script().read_text(),
            "__atrex_abba.py": Path(__file__).with_name("abba_remote.py").read_text()},
        "env_vars": request.get("env_vars", {}), "timeout_s": timeout, "recycle": True,
        "dev_intent": "sanitize" if parameters.get("sanitize") else "profile_adhoc"}
    return dict(request, **{DIAGNOSTIC: payload})


def run(gateway, args, request, kind, queue_wait_grace, *, identity_extra=None):
    """Run any typed or source-tree operation through the published SDK."""
    diagnostic = request.get(DIAGNOSTIC)
    if kind == "run" and diagnostic is None:
        return run_eval_sdk(
            gateway,
            args,
            request,
            queue_wait_grace,
            identity_extra=identity_extra,
        )
    sdk_kind = "dev" if diagnostic is not None else {
        "check": "compile",
        "disassemble": "disassemble",
        "profile": "profile",
    }.get(kind, kind)
    process = run_sdk_job(
        gateway,
        args,
        diagnostic or request,
        sdk_kind,
        queue_wait_grace,
        identity_extra=identity_extra,
    )
    if diagnostic and process.returncode == 0:
        from .source_diagnostics import DIAGNOSTIC_PREFIX
        from aka.light.experiment.store import EvidenceUnavailable
        job = gateway.parse_job_response(process.stdout)
        if not job or not isinstance(job.get("result"), dict):
            raise EvidenceUnavailable("Source-tree diagnostic returned no complete Job result")
        for line in reversed((job.get("result", {}).get("stdout") or "").splitlines()):
            if line.startswith(DIAGNOSTIC_PREFIX):
                try:
                    result = json.loads(line[len(DIAGNOSTIC_PREFIX):])
                    if result.get("operation") != kind or not isinstance(result.get("passed"), bool):
                        continue
                except (ValueError, TypeError, AttributeError):
                    continue
                job["source_tree_execution"] = job["result"]
                job["result"] = result
                return subprocess.CompletedProcess(process.args, 0 if result["passed"] else 1, json.dumps(job), process.stderr)
        raise EvidenceUnavailable("Source-tree diagnostic returned no complete result; inspect its saved Job")
    return process
