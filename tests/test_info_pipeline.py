from pathlib import Path
import json

from agent.info_pipeline import generate_info_answer


def test_generate_info_answer_basic(tmp_path: Path):
    readme = tmp_path / "README.md"
    readme.write_text("# Demo App\n\nA demo project for testing.\n\nMore details.\n")
    pkg = tmp_path / "package.json"
    pkg.write_text(json.dumps({"scripts": {"dev": "vite", "start": "node server.js"}}))

    info = generate_info_answer(tmp_path)
    rendered = info.render()

    assert "Demo App" in rendered
    assert "npm run dev" in rendered or "pnpm run dev" in rendered or "yarn run dev" in rendered
    assert "Project Summary:" in rendered
    assert "How to Start:" in rendered


def test_generate_info_answer_readme_commands(tmp_path: Path):
    readme = tmp_path / "README.md"
    readme.write_text(
        "# Demo\n\n"
        "## Quick Start\n\n"
        "```bash\n"
        "npm install\n"
        "npm run dev\n"
        "```\n"
    )
    info = generate_info_answer(tmp_path)
    rendered = info.render()
    assert "npm run dev" in rendered
