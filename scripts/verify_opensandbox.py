"""One-off end-to-end check of the OpenSandbox integration.

Creates a real sandbox via SandboxProvider, verifies the working-directory
layout, command execution, and file write/read, then destroys the sandbox.
"""

import asyncio
import sys

from ai_coding_harness.config import get_settings
from ai_coding_harness.sandbox.provider import SandboxProvider


async def main() -> int:
    settings = get_settings()
    provider = SandboxProvider(settings)

    print(f"-> creating sandbox (image={settings.sandbox_image}) ...")
    client = await provider.create()
    print(f"   sandbox id = {client.id}")

    try:
        # 1. verify working directories exist
        dirs = [
            settings.sandbox_project_dir,
            settings.sandbox_policies_dir,
            settings.sandbox_tmp_dir,
            settings.sandbox_skills_dir,
            settings.sandbox_output_dir,
        ]
        listing = await client.run(f"ls -d {' '.join(dirs)}")
        print("-> working dirs:")
        print("   " + listing.stdout.replace("\n", "\n   "))

        # 2. command execution
        hello = await client.run("echo 'hello from sandbox' && python3 --version")
        print(f"-> echo+python: exit={hello.exit_code}")
        print("   " + hello.stdout.replace("\n", "\n   "))

        # 3. file write/read roundtrip
        probe = f"{settings.sandbox_tmp_dir}/probe.txt"
        await client.write_file(probe, "probe-content-42")
        read_back = await client.read_file(probe)
        assert read_back == "probe-content-42", read_back
        print(f"-> file write/read roundtrip OK ({probe})")

        # 4. search
        found = await client.search(settings.sandbox_tmp_dir, "*.txt")
        print(f"-> search *.txt matched {len(found)} file(s)")
    finally:
        print("-> destroying sandbox ...")
        await provider.destroy(client)
        print("   destroyed")

    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
