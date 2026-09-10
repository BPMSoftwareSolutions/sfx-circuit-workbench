# Deploy the circuit workbench to Hugging Face

This repository owns the Space packaging, upload, Docker configuration, and release
verification. Run the commands below from the **sfx-circuit-workbench repository root**.
The target is the existing private
[BPMSoftwareSolutions/SideFX Space](https://huggingface.co/spaces/BPMSoftwareSolutions/SideFX).

## Deployment files

| File | Purpose |
| --- | --- |
| [tools/hf_space.py](../tools/hf_space.py) | Package, check, deploy, inspect status, and verify a release. |
| [huggingface/Dockerfile](huggingface/Dockerfile) | Build the Next.js host and run its standalone server on port 7860 as the `node` user. |
| [huggingface/README.md](huggingface/README.md) | Space metadata, including the full-width layout and mini header. |
| [huggingface/layout.tsx](huggingface/layout.tsx) | Space host document layout. |
| [huggingface/.dockerignore](huggingface/.dockerignore) | Exclude local dependency trees and environment files from Docker. |
| [huggingface/space.json](huggingface/space.json) | Explicit Space repository, branch, and application origin. |
| [requirements.txt](requirements.txt) | Deployment client dependency. |

The packager reads a bounded set of Next.js host files from the `sfx-platform`
checkout. It takes the workbench assets and dialog publication directly from this
repository's build output. It does not call the platform's deployment scripts or
require copying the workbench into that checkout first.

The existing Azure invocation service remains a separate deployment. This tooling
deploys its Hugging Face renderer and authenticated host adapter; it does not rebuild
the SQL runtime, change service capacity, or move provider credentials into the Space.

## Prerequisites and credentials

- Python 3.12 and the normal workbench build dependencies.
- The `sidefx-ui`, `sfx-platform`, and database/estate workspaces consumed by the existing
  build pipeline, with the current publication generated in `sfx-platform/generated`.
- An existing private Docker Space and a Hugging Face token with write access to it.
- Docker is optional for a local container build; Hugging Face builds the uploaded image.

```powershell
python -m pip install -r deploy/requirements.txt
```

The tooling reads `HF_TOKEN`, then `HF_ACCESS_TOKEN`, then the Windows user-level
`HF_ACCESS_TOKEN` variable, then the token saved by `hf auth login`. Tokens are never
written into the package or deployment receipt.

The Space must already have these secrets configured in its Settings:

| Secret | Value supplied by the deployment operator |
| --- | --- |
| `SIDEFX_INVOCATION_ENDPOINT` | HTTPS endpoint of the existing invocation service. |
| `SIDEFX_SERVICE_TOKEN` | Existing shared service credential. |
| `SIDEFX_LAB_ORIGIN` | `https://bpmsoftwaresolutions-sidefx.hf.space` |

Normal deployment preserves these secrets. It does not read a local `runtime.env`
file or overwrite secrets as a side effect of uploading UI files. The Space remains
private; the uploader refuses a public target.

## Build and prepare a release

Refresh the retained database selection when the capability publication changes:

```powershell
node tools/read_invocation_authority.mjs
```

Build the interactive workbench, then package it with its host:

```powershell
python tools/package_interactive_workbench.py
python tools/hf_space.py package --platform-root C:/lab/repos/sfx-platform
python tools/hf_space.py check
```

`--platform-root` also accepts the `SFX_PLATFORM_ROOT` environment variable and defaults
to the path above. It configures the deployment packager's host input; the existing
authority/build adapters retain their own workspace configuration.

Every packaging run creates a fresh `build/huggingface/bundle-*` directory and writes
`build/huggingface/package.receipt.json`. The receipt records all packaged file digests,
the workbench package version, and the deployment target. `check` verifies that the
staged bytes still match; it performs no upload and requires no credentials. Build
outputs and local deployment receipts are ignored by Git.

The workbench asset fingerprint and publication IDs must agree before packaging.
Environment files, private-key files, dependency directories, and symlinks are refused.
The platform's full source tree and local credential files are not copied.

Optionally build the exact Docker context before uploading:

```powershell
$release = Get-Content build/huggingface/package.receipt.json -Raw | ConvertFrom-Json
docker build -t sidefx-circuit-workbench:check $release.artifact
```

## Deploy and verify

```powershell
python tools/hf_space.py deploy --message "Deploy circuit workbench update"
```

This uploads the checked artifact to the configured Space's `main` branch. It pins
the current remote parent commit so a concurrent update cannot be silently overwritten.
Only obsolete files under the renderer-owned `public/workbench/` directory are removed;
other remote files are preserved.

The command waits up to ten minutes for **the uploaded commit** to be running, then
checks the Space layout settings, served workbench document, and live package version.
It writes `build/huggingface/deployment.json` immediately after upload, retaining the
commit even if the build fails or verification times out. `verified: true` is recorded
only after those release checks pass.

Inspect progress or retry verification without uploading again:

```powershell
python tools/hf_space.py status
python tools/hf_space.py verify --wait-seconds 600
```

An upload failure whose outcome is uncertain should be inspected in the Space's commit
history before another upload is attempted. A successful upload followed by a timeout
can be reconciled with `verify` using the retained deployment receipt.

Deployment verification establishes which renderer is running. Run the existing hosted
browser checks for the behavior changed by a release, for example:

```powershell
# Browser checks currently consume HF_TOKEN from the process environment.
$env:HF_TOKEN = [Environment]::GetEnvironmentVariable('HF_ACCESS_TOKEN', 'User')
python tests/browser_viewport.py --origin https://bpmsoftwaresolutions-sidefx.hf.space --label hosted
Remove-Item Env:\HF_TOKEN
```

For capability changes, use the invocation, interaction, and recovery checks documented
in the [delivery record](../docs/hosted-workbench-delivery.md). A deployment receipt does
not replace that functional evidence. Copy a reviewed release receipt into `evidence/host/`
when retaining it with the corresponding source changes.

## Recover a previous release

Keep the prior bundle and its package receipt until a new release is accepted. Upload
the retained artifact as a new Space commit to restore it without rewriting history:

```powershell
python tools/hf_space.py deploy --receipt build/huggingface/previous-package.receipt.json --message "Restore previous workbench release"
```

`previous-package.receipt.json` must be a saved copy of a real earlier packaging receipt,
and its referenced bundle must still exist and pass the digest checks. The tool does
not synthesize an old build from a version label.

## Tooling checks

```powershell
python -m unittest discover -s tests -p test_hf_space.py
```

These checks cover changed artifacts, excluded files, private-target enforcement,
remote parent protection, bounded cleanup, and refusal to certify an older or unrelated
running commit. They use a simulated Hub client and do not publish anything.

Hugging Face references: [Space configuration](https://huggingface.co/docs/hub/spaces-config-reference)
and [Hub API](https://huggingface.co/docs/huggingface_hub/package_reference/hf_api).
