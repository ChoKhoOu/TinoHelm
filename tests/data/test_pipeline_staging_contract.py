from pathlib import Path


def test_pipeline_uses_ingest_staging_root_for_intermediate_writes():
    source = Path("src/tinohelm/data/pipeline.py").read_text()

    assert 'return str(Path(self.catalog_path) / ".ingest-staging" / ingest_run_id)' in source
    assert 'self._active_write_catalog_path = stage_root' in source
    assert 'self._active_write_catalog_path = self.catalog_path' in source


def test_pipeline_commits_staged_outputs_before_db_catalog_update():
    source = Path("src/tinohelm/data/pipeline.py").read_text()

    commit_call = source.index("committed_paths = self._commit_staged_outputs(")
    db_update = source.index("update_task = asyncio.create_task(self._update_db_catalog(")

    assert commit_call < db_update
