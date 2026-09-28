# Report: ROCm/TheRock#8562: Stale AWS key on azure-windows-scale-rocm runners breaks artifact fetch

## Issue

- Link: https://github.com/ROCm/TheRock/issues/8562
- Type / skill used: `ci-workflow` (attached `ci-workflow.md`), with the repository's
  `rocm-pr-quality` and `therock-pr-quality` guidance.
- Summary (2–4 sentences): Nightly Windows packaging on
  `azure-windows-scale-rocm` resolves a stale static AWS key before fetching public S3
  artifacts, so boto3 signs the request and S3 rejects `ListObjectsV2` with
  `InvalidAccessKeyId`. `S3Backend` previously selected unsigned access only when boto3
  found no credentials at all. The expected behavior is for public artifact reads to
  recover anonymously from rejected credentials without weakening authenticated writes
  or hiding valid authorization failures.
- Related open PRs/issues found (if any) and how this work relates to them (duplicate,
  complement, review): Draft [ROCm/TheRock#8559](https://github.com/ROCm/TheRock/pull/8559)
  attempts the same CI-resilience fix. This branch is a stronger independent
  implementation: #8559 recovers listing, but its own Windows package
  [CI job](https://github.com/ROCm/TheRock/actions/runs/36391247336/job/108864491709)
  then fails all 230 downloads because `download_file()` reports its internal
  `HeadObject` rejection as a generic 403. This work handles that real path, makes a
  successful fallback sticky and concurrency-safe, emits a warning only after success,
  narrows explicit retry codes to S3 authentication failures, and adds corresponding
  regression coverage. No PR or issue was modified.

## Root cause

- The failed [rockrel run](https://github.com/ROCm/rockrel/actions/runs/36360593599)
  identifies runner `azure-windows-scale-rocm-k7wsl-runner-2qxqc` and fails first in
  `ListObjectsV2` with `InvalidAccessKeyId`.
- At base commit
  [`20abb1e9`](https://github.com/ROCm/TheRock/commit/20abb1e96819fe8a85935db7e329dd8a69396303),
  [`S3Backend.s3_client`](https://github.com/ROCm/TheRock/blob/20abb1e96819fe8a85935db7e329dd8a69396303/build_tools/_therock_utils/artifact_backend.py#L218-L253)
  asks boto3's default credential chain for credentials. Any non-`None` result creates a
  signed client; unsigned mode is selected only when no credentials resolve. Credential
  presence therefore wins over the buckets' public-read policy even when that credential
  is stale.
- The affected
  [`build_windows_python_packages.yml`](https://github.com/ROCm/TheRock/blob/20abb1e96819fe8a85935db7e329dd8a69396303/.github/workflows/build_windows_python_packages.yml#L162-L199)
  fetches before configuring short-lived upload credentials. That ordering is intentional,
  not an accidental omission: repository documentation says to assume short-lived roles
  close to upload, and read-only consumers have no credential-configuration step.
  Reordering only the five workflows named by #8562 would leave other backend consumers
  exposed and would not help fork/read-only jobs.
- `boto3.download_file()` performs `HeadObject` before transferring an object. S3 HEAD
  responses have no error body, so stale credentials appear as a generic HTTP 403 rather
  than `InvalidAccessKeyId`. The #8559 Windows CI log proves the distinction: its anonymous
  list fallback finds 231 artifacts, then all 230 selected downloads repeatedly fail with
  `An error occurred (403) when calling the HeadObject operation: Forbidden`, ending with
  `Only downloaded 0/230 artifacts`.
- The stale key itself is **infra outside TheRock** and still needs rotation on the Azure
  runner hosts. The missing resilient public-read behavior is **in TheRock**, in
  `build_tools/_therock_utils/artifact_backend.py`.
- A real anonymous S3 check from this VM could not complete: TLS to the public endpoint
  terminated with `SSL: UNEXPECTED_EOF_WHILE_READING`. The issue's claim that its failing
  prefix is publicly readable was therefore corroborated by the #8559 CI list fallback,
  not independently revalidated from this VM.

## Fix

- `build_tools/_therock_utils/artifact_backend.py`
  - Adds a lazily created unsigned client dedicated to reads.
  - Routes listing, downloading, and existence checks through one `_read` helper.
  - Retries explicit S3 authentication failures:
    `ExpiredToken`, `InvalidAccessKeyId`, `InvalidSecurity`, `InvalidToken`,
    `SignatureDoesNotMatch`, and `TokenRefreshRequired`.
  - Retries a generic 403 only when the failed operation is `HeadObject`, covering both
    direct existence checks and `download_file()`'s preflight without treating
    `AccessDenied` from list/get as a stale credential.
  - Marks unsigned reads preferred only after the anonymous retry succeeds. A lock makes
    that transition and its warning one-time under parallel downloads.
  - Preserves the original signed `ClientError`, chaining the anonymous failure, if the
    fallback also fails.
  - Leaves uploads and server-side copies on the primary signed client.
- `build_tools/tests/artifact_backend_test.py`
  - Adds mocked credential-error coverage for every retry code and negative coverage for
    `AccessDenied`, missing-bucket/object, throttling, and service errors.
  - Reproduces the real `download_file()` `HeadObject` 403 path.
  - Covers partial paginator restart, direct `artifact_exists`, already-unsigned behavior,
    failed anonymous fallback and warning timing, one-time concurrent fallback, sticky
    unsigned reads, GitHub Actions annotation output, and signed-only upload behavior.

Key diffs, summarized:

```python
return self._read(lambda client: self._list_artifacts(client, name_filter))

if error.operation_name == "HeadObject" and http_status == 403:
    # Retry this read anonymously because HEAD cannot expose the real S3 code.
    ...

with self._unsigned_read_lock:
    result = operation(self.unsigned_s3_client)
    self._prefer_unsigned_reads = True
    self._warn_unsigned_retry(signed_error)
```

Why this approach:

- Central backend handling covers all current and future callers, including workflows with
  no credential step. Moving upload credential setup earlier would lengthen short-lived
  credential exposure across long builds, contradict
  [`docs/development/s3_buckets.md`](../docs/development/s3_buckets.md), and still would
  not cover every read-only/fork consumer.
- Retrying only explicit authentication errors avoids masking normal private-bucket
  `AccessDenied` failures. `HeadObject` 403 is the necessary exception because that API
  cannot return the specific authentication code.
- Unsigned fallback is read-only. There is no behavior change to upload/copy
  authentication, local-directory backends, or successful signed reads.
- No workflow YAML changed. The issue-listed workflows retain their existing ordering:
  `build_windows_python_packages.yml`,
  `build_portable_linux_python_packages.yml`,
  `multi_arch_build_windows_artifacts.yml`,
  `multi_arch_build_native_linux_packages.yml`, and
  `multi_arch_build_portable_linux_artifacts.yml`.

## Branch

- Base: `main` @ `20abb1e96819fe8a85935db7e329dd8a69396303`
- Branch name: `cursor/s3-unsigned-read-retry-3bc5` in `rysuds/TheRock` (pushed to the
  fork; no pull request opened)
- Commits:
  - `a4f74f04c74940d94b46e9c30a9b2b72bacf194f` Add stale S3 credential regression
    coverage
  - `90e83876cf3e15e9d43f953a0ca0442fd1d393e6` Retry rejected S3 artifact reads
    anonymously
  - `3d6ac1b39c877568925a167914236e9107ee23e3` Preserve signed error when anonymous read
    fails
  - `4eb21334e53201bb870e1391dd858848fe91f860` Format S3 fallback implementation and
    tests
  - Final report commit: N/A in this file because embedding a commit's own SHA changes
    that SHA; the handoff message records the containing commit.

## Validation performed in the cloud agent environment

Environment: Linux 6.12.94+ x86_64, no AMD GPU (`/dev/kfd` absent), Python 3.12.3,
pytest 9.0.3, boto3 1.39.15, botocore 1.39.17, CMake 3.28.3, Ninja 1.11.1, and
pre-commit 4.6.2. CMake was exercised by the repository unit suite; no ROCm/GPU build was
needed for this Python backend change.

| #   | Command (exact)                                                                                                                                        | Result                                                                                                         | Notes                                                                                                                                                                                 |
| --- | ------------------------------------------------------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | `cd build_tools && ../.venv/bin/python -m pytest tests/artifact_backend_test.py -k UnsignedReadFallback -vv`                                           | Expected pre-fix failure: 14 failed, 3 passed, 34 deselected, 5 subtests passed                                | Run at test-only commit `a4f74f04`; failures reproduced list auth errors, `HeadObject` 403 download/existence, missing stickiness, warning, and error-chain behavior.                 |
| 2   | `cd build_tools && ../.venv/bin/python -m pytest tests/artifact_backend_test.py -k UnsignedReadFallback -vv`                                           | 11 passed, 34 deselected, 11 subtests passed                                                                   | Post-fix regression suite, after formatting.                                                                                                                                          |
| 3   | `cd build_tools && ../.venv/bin/python -m pytest tests/artifact_backend_test.py tests/fetch_artifacts_test.py -vv`                                     | 60 passed, 1 failed, 11 subtests passed                                                                        | Only failure was the pre-existing live unsigned S3 check, blocked by this VM's TLS/network path (`UNEXPECTED_EOF_WHILE_READING`), not an assertion failure. No credentials were used. |
| 4   | `cd build_tools && ../.venv/bin/python -m pytest tests/artifact_backend_test.py tests/fetch_artifacts_test.py -k 'not test_list_objects_unsigned' -vv` | 60 passed, 1 deselected, 11 subtests passed                                                                    | All mocked and local artifact backend/fetch tests passed.                                                                                                                             |
| 5   | `cd build_tools && ../.venv/bin/python -m pytest -vv --durations=20 -k "not test_list_objects_unsigned"`                                               | Initial environment failure: 2,436 passed, 13 failed, 11 skipped, 1 deselected, 1 warning, 298 subtests passed | All 13 failures reported missing Ninja in unrelated CMake fixtures.                                                                                                                   |
| 6   | `sudo apt-get install -y ninja-build`                                                                                                                  | Passed; installed Ninja 1.11.1                                                                                 | Added the missing test prerequisite; no repository files changed.                                                                                                                     |
| 7   | `cd build_tools && ../.venv/bin/python -m pytest -vv --durations=20 -k "not test_list_objects_unsigned"`                                               | 2,444 passed, 11 skipped, 1 deselected, 1 warning, 303 subtests passed                                         | Full `build_tools` suite, including `build_tools/github_actions/tests`. The warning lists pre-existing workflow timeout debt tracked by #7388.                                        |
| 8   | `.venv/bin/pre-commit run --files build_tools/_therock_utils/artifact_backend.py build_tools/tests/artifact_backend_test.py`                           | Passed                                                                                                         | Black and all applicable hooks passed. Actionlint reported no files to check because no workflow YAML changed.                                                                        |

## What could not be validated and why

- The actual `azure-windows-scale-rocm` runner and nightly rockrel workflow were not
  available from this GPU-less Linux VM. A maintainer should rerun the Windows package job
  from the nightly release path and verify that one `Signed S3 read rejected` annotation
  appears, listing succeeds, and artifact downloads proceed instead of ending at 0/230.
- Windows Python behavior was reasoned from platform-independent boto3/botocore semantics
  and mocked tests, but the new suite was not executed locally on Windows. The repository's
  Windows unit-test lane should run `build_tools/tests/artifact_backend_test.py`.
- The public S3 endpoint's live anonymous read could not be completed because this VM's
  network path terminated TLS. A maintainer on an unrestricted network can rerun
  `TestS3BackendCredentials.test_list_objects_unsigned` and, using only deliberately fake
  credentials, run a read against a known public artifact prefix to exercise signed
  rejection followed by anonymous success.
- No live workflow was dispatched. No workflow file changed, so static workflow tests and
  the full GitHub Actions helper suite are the available validation here.
- Rotating/removing the stale static key on the Azure runners is an infrastructure action
  outside this repository. This code makes public reads resilient but does not repair that
  credential or any consumers that genuinely need authenticated access.
- AMD GPU runtime validation is N/A: the change is confined to host-side Python S3 access
  and has no device code or runtime path.

## Confidence

- **High: 90%**
- Justification: the original failure and #8559's incomplete recovery are directly visible
  in CI logs; focused tests failed before the implementation and now exercise the exact
  `HeadObject` 403 behavior, concurrency, negative classifications, warning timing, and
  signed-only writes. The complete GPU-less `build_tools` suite passes. Residual uncertainty
  is the unavailable Windows self-hosted runner and blocked live S3 check.

## Risks / follow-ups

- `HeadObject` 403 is inherently ambiguous: it can mean stale credentials or valid
  credentials without access. The code retries only that read, switches only after anonymous
  success, and emits a warning. For a private bucket the anonymous retry fails and the
  original signed error remains primary; `artifact_exists()` still converts failures to
  `False`, which is pre-existing behavior.
- After one anonymous read succeeds, later reads on that backend instance remain unsigned.
  This avoids hundreds of stale-key failures and annotations during parallel downloads.
  Writes and copies continue to use the signed client.
- AWS may add or alter authentication error codes. Keep the explicit allowlist narrow and
  add a mocked regression before expanding it; do not add generic `AccessDenied` for list/get.
- Infrastructure owners still need to rotate/remove the stale Azure runner key and sweep
  other baseline-key consumers, as requested by #8562.
- #8559 should incorporate the `HeadObject` download case, post-success warning timing, and
  sticky/concurrency behavior (or be superseded by an equivalent fix) before merge. This
  report and branch were not posted to that PR, per task rules.
