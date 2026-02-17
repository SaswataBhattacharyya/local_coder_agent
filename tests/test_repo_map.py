from pathlib import Path
from indexer.indexer import SymbolIndexer
from indexer.dep_graph import DependencyGraph
from indexer.repo_map import RepoMapBuilder


def test_repo_map_build(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("import os\n\nclass A: pass\n")
    (repo / "b.py").write_text("from a import A\n")

    idx = SymbolIndexer(repo_root=repo, db_path=repo / ".agent" / "index.sqlite")
    idx.index_all()

    dep = DependencyGraph(repo_root=repo, db_path=repo / ".agent" / "deps.sqlite")
    dep.init_db()
    dep.update_file(repo / "a.py")
    dep.update_file(repo / "b.py")

    builder = RepoMapBuilder(repo_root=repo, index_db=idx.db_path, dep_db=dep.db_path)
    out = builder.build(repo / ".agent" / "state" / "sessions" / "s" / "branches" / "main" / "repo_map")
    assert out["file_count"] >= 2
    assert any(m["deps"] >= 1 for m in out["top_modules"])
