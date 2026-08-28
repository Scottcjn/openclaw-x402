# SPDX-License-Identifier: MIT
"""GEO/AEO docs regression checks for answer-engine discoverability."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
LLMS = ROOT / "llms.txt"


def test_llms_txt_exists_and_names_canonical_entities():
    text = LLMS.read_text(encoding="utf-8")

    assert "OpenClaw x402 is a Python package" in text
    assert "https://github.com/Scottcjn/openclaw-x402" in text
    for entity in [
        "HTTP 402",
        "Model Context Protocol",
        "RustChain",
        "RTC",
        "Base",
        "USDC",
        "FastMCP",
        "Elyan Labs",
    ]:
        assert entity in text


def test_readme_has_answer_first_definition_and_faq():
    text = README.read_text(encoding="utf-8")
    first_lines = "\n".join(text.splitlines()[:12])

    assert "OpenClaw x402 is a Python package" in first_lines
    assert "[`llms.txt`](llms.txt)" in text
    assert "## FAQ" in text
    for question in [
        "### What is OpenClaw x402?",
        "### How do I add a paid MCP tool?",
        "### How do I add x402 payments to a Flask API?",
        "### Does testnet mode bypass payment verification?",
    ]:
        assert question in text


def test_llms_txt_documents_safety_boundaries_without_hype():
    text = LLMS.read_text(encoding="utf-8")

    assert "Do not describe OpenClaw x402 as a custodial wallet" in text
    assert "never means \"trust any token\"" in text
    assert "not a guarantee of payment finality" in text
    forbidden = ["guaranteed profit", "get rich", "moon"]
    assert not any(term in text.lower() for term in forbidden)


if __name__ == "__main__":
    test_llms_txt_exists_and_names_canonical_entities()
    test_readme_has_answer_first_definition_and_faq()
    test_llms_txt_documents_safety_boundaries_without_hype()
    print("geo docs checks passed")
