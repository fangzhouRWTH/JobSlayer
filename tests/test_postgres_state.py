from __future__ import annotations

import os
import re
import unittest
from uuid import uuid4
from unittest.mock import patch

from jobslayer.adapters.postgres_state import PostgresControlPlaneStore
from jobslayer.persistence import StateStoreError
from tests.state_store_contract import ControlPlaneStoreContract


class PostgresAdapterConfigurationTests(unittest.TestCase):
    def test_rejects_blank_dsn_and_unsafe_schema(self) -> None:
        with self.assertRaises(ValueError):
            PostgresControlPlaneStore(" ")
        with self.assertRaises(ValueError):
            PostgresControlPlaneStore("postgresql://fixture", schema="bad-name")

    def test_missing_optional_driver_fails_without_exposing_dsn(self) -> None:
        store = PostgresControlPlaneStore(
            "postgresql://secret-user:secret-password@example.invalid/jobslayer"
        )
        with patch("jobslayer.adapters.postgres_state.psycopg", None):
            with self.assertRaisesRegex(StateStoreError, "optional 'postgres'") as caught:
                store.migrate()
        self.assertNotIn("secret-password", str(caught.exception))


class PostgresSharedStateStoreContractTests(
    ControlPlaneStoreContract,
    unittest.TestCase,
):
    @classmethod
    def setUpClass(cls) -> None:
        cls.dsn = os.environ.get("JOBSLAYER_TEST_POSTGRES_DSN", "").strip()
        if not cls.dsn:
            raise unittest.SkipTest("JOBSLAYER_TEST_POSTGRES_DSN is not configured")
        import psycopg

        cls.psycopg = psycopg

    def setUp(self) -> None:
        self.schema = f"jobslayer_test_{uuid4().hex}"
        self.assertRegex(self.schema, re.compile(r"^[a-z_][a-z0-9_]*$"))
        with self.psycopg.connect(self.dsn, autocommit=True) as connection:
            connection.execute(
                self.psycopg.sql.SQL("CREATE SCHEMA {}").format(
                    self.psycopg.sql.Identifier(self.schema)
                )
            )
        self.store = PostgresControlPlaneStore(self.dsn, schema=self.schema)
        self.store.migrate()

    def tearDown(self) -> None:
        with self.psycopg.connect(self.dsn, autocommit=True) as connection:
            connection.execute(
                self.psycopg.sql.SQL("DROP SCHEMA {} CASCADE").format(
                    self.psycopg.sql.Identifier(self.schema)
                )
            )

    def reopen_store(self) -> PostgresControlPlaneStore:
        reopened = PostgresControlPlaneStore(self.dsn, schema=self.schema)
        reopened.migrate()
        return reopened

    def test_database_rejects_changes_to_owned_truth(self) -> None:
        with self.store.transaction(
            task_id="postgres-append-only",
            run_id="postgres-append-only-run",
            expected_task_sequence=0,
            expected_run_sequence=0,
        ) as transaction:
            from jobslayer.domain.models import ActorType, TaskState
            from jobslayer.persistence import OutboxEvent
            from jobslayer.workflow.kernel import WorkflowKernel

            WorkflowKernel(transaction.journal).transition(
                task_id="postgres-append-only",
                to_state=TaskState.PLANNED,
                actor_type=ActorType.SYSTEM,
                actor_id="planner",
                reason="append only",
            )
            transaction.enqueue(
                OutboxEvent(
                    event_id="postgres-append-only-event",
                    topic="control-plane.changed",
                    task_id="postgres-append-only",
                    run_id="postgres-append-only-run",
                    payload={},
                )
            )
            transaction.commit()

        connection = self.store._connect()
        try:
            for statement in (
                "UPDATE workflow_transitions SET task_id = 'changed'",
                "DELETE FROM workflow_transitions",
                "TRUNCATE workflow_transitions",
            ):
                with self.assertRaises(self.psycopg.Error):
                    connection.execute(statement)
                connection.rollback()
        finally:
            connection.close()
