from __future__ import annotations

from datetime import date, datetime
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tinohelm.data import coverage as cov
from tinohelm.db.models import Base, DataFetchJob


class TestPlanSubmissionSlices:
    async def _build_factory(self):
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        return factory, engine

    def _row(
        self,
        *,
        job_id: str,
        status: str,
        start_date: date,
        end_date: date,
    ) -> DataFetchJob:
        return DataFetchJob(
            job_id=job_id,
            batch_id=None,
            symbol="BTCUSDT",
            data_type="klines",
            interval="1m",
            start_date=start_date,
            end_date=end_date,
            asset_class="um",
            status=status,
            created_at=datetime(2026, 1, 1, 12, 0, 0),
        )

    async def test_excludes_current_job_but_still_subtracts_other_active_rows(self, monkeypatch):
        factory, engine = await self._build_factory()
        start = date(2024, 1, 1)
        end = date(2024, 1, 10)
        current_job_id = str(uuid4())

        monkeypatch.setattr(
            cov,
            "plan_catalog_missing_slices",
            lambda **_: [(start, end)],
        )

        try:
            async with factory() as db:
                db.add(
                    self._row(
                        job_id=current_job_id,
                        status="running",
                        start_date=start,
                        end_date=end,
                    )
                )
                db.add(
                    self._row(
                        job_id=str(uuid4()),
                        status="queued",
                        start_date=date(2024, 1, 4),
                        end_date=date(2024, 1, 6),
                    )
                )
                await db.commit()

                slices = await cov.plan_submission_slices(
                    db=db,
                    catalog_path="irrelevant",
                    symbol="BTCUSDT",
                    data_type="klines",
                    interval="1m",
                    start=start,
                    end=end,
                    exclude_job_ids={current_job_id},
                )

            assert slices == [
                (date(2024, 1, 1), date(2024, 1, 3)),
                (date(2024, 1, 7), date(2024, 1, 10)),
            ]
        finally:
            await engine.dispose()
