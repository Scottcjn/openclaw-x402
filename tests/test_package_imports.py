import subprocess
import sys
import textwrap


def test_mcp_server_import_does_not_require_flask():
    script = textwrap.dedent(
        """
        import importlib.abc
        import sys

        class BlockFlask(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname == "flask" or fullname.startswith("flask."):
                    raise ModuleNotFoundError("No module named 'flask'", name="flask")
                return None

        sys.meta_path.insert(0, BlockFlask())
        import openclaw_x402.mcp_server
        print("mcp import ok")
        """
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert "mcp import ok" in result.stdout


def test_top_level_middleware_export_explains_missing_flask():
    script = textwrap.dedent(
        """
        import importlib.abc
        import sys

        class BlockFlask(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname == "flask" or fullname.startswith("flask."):
                    raise ModuleNotFoundError("No module named 'flask'", name="flask")
                return None

        sys.meta_path.insert(0, BlockFlask())
        from openclaw_x402 import X402Middleware
        """
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert "pip install openclaw-x402[flask]" in result.stderr
