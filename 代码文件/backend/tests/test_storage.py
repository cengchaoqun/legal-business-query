"""持久化契约：真实数据库验证认证、幂等、互斥与终态保存。"""
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select, update

from backend.app.storage import Storage, StorageConflict


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.storage = Storage("sqlite:///" + (Path(self.directory.name) / "state.db").as_posix())
        self.storage.initialize()
        self.user = self.storage.seed_user("analyst", "示例密码", "analyst")
        self.other = self.storage.seed_user("other", "另一密码", "admin")
        self.conversation = self.storage.create_conversation(self.user["id"])

    def tearDown(self):
        self.storage.engine.dispose()
        self.directory.cleanup()

    def begin(self, request_id="req-1", **kwargs):
        return self.storage.begin_query(self.conversation["id"], self.user["id"], request_id, "今年回款多少？", **kwargs)

    def test_password_and_session_secrets_are_not_stored_in_plaintext(self):
        self.assertEqual(self.storage.authenticate("analyst", "示例密码"), self.user)
        self.assertIsNone(self.storage.authenticate("analyst", "错误密码"))
        self.assertIsNone(self.storage.authenticate("missing", "错误密码"))
        token = self.storage.create_session(self.user["id"])
        self.assertEqual(self.storage.get_session(token), self.user)
        with self.storage.engine.begin() as connection:
            password = connection.execute(select(self.storage.users.c.password_hash)).scalar()
            stored_token = connection.execute(select(self.storage.sessions.c.token_hash)).scalar()
        self.assertNotIn("示例密码", password)
        self.assertNotEqual(stored_token, token)
        self.storage.delete_session(token)
        self.assertIsNone(self.storage.get_session(token))

    def test_seed_preserves_existing_password_and_session_expires(self):
        again = self.storage.seed_user("analyst", "should-not-reset", "admin")
        self.assertEqual(again, self.user)
        self.assertIsNotNone(self.storage.authenticate("analyst", "示例密码"))
        token = self.storage.create_session(self.user["id"])
        with self.storage.engine.begin() as connection:
            connection.execute(update(self.storage.sessions).values(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
        self.assertIsNone(self.storage.get_session(token))

    def test_idempotency_and_conversation_exclusion(self):
        first, created = self.begin()
        self.assertTrue(created)
        repeated, created = self.begin()
        self.assertFalse(created)
        self.assertEqual(first["query_id"], repeated["query_id"])
        with self.assertRaises(StorageConflict):
            self.begin("different")
        other_conversation = self.storage.create_conversation(self.user["id"])
        with self.assertRaises(StorageConflict):
            self.storage.begin_query(other_conversation["id"], self.user["id"], "req-1", "另一个问题")
        self.assertIsNone(self.storage.get_conversation(self.conversation["id"], self.other["id"]))
        with self.assertRaises(StorageConflict):
            self.storage.begin_query(self.conversation["id"], self.other["id"], "stolen", "越权")

    def test_terminal_state_cannot_be_overwritten_and_conditions_are_atomic(self):
        query, _ = self.begin()
        self.storage.update_stage(query["query_id"], "generating")
        conditions = {"metric_ids": ["net_receipts"], "group_by": ["team"]}
        decision = {"action": "clarify", "question": "哪一年？", "reason": "缺少时间", "sql": None, "metric_ids": []}
        self.storage.finish_query(query["query_id"], "clarify", decision=decision, conditions=conditions)
        conversation = self.storage.get_conversation(self.conversation["id"], self.user["id"])
        self.assertEqual(conversation["confirmed_conditions"], conditions)
        self.assertEqual(conversation["pending_clarification"], decision)
        self.assertIsNone(conversation["active_query_id"])
        self.storage.finish_query(query["query_id"], "failed", error_code="LATE")
        self.storage.update_stage(query["query_id"], "executing")
        saved = self.storage.get_query(query["query_id"])
        self.assertEqual(saved["status"], "clarify")
        self.assertIsNone(saved["error_code"])
        followup, _ = self.begin("followup")
        self.assertEqual(followup["parent_query_id"], query["query_id"])
        self.storage.finish_query(followup["query_id"], "succeeded", result={"rows": [["12.30", None]]})
        self.assertIsNone(self.storage.get_conversation(self.conversation["id"], self.user["id"])["pending_clarification"])
        self.assertEqual(len(self.storage.list_queries(self.conversation["id"])), 2)
        self.assertEqual(len(self.storage.all_query_runs()), 2)
        json.dumps(self.storage.all_query_runs(), ensure_ascii=False)

    def test_expired_processing_is_failed_and_releases_lock(self):
        first, _ = self.begin(timeout_seconds=-1)
        saved = self.storage.get_query(first["query_id"])
        self.assertEqual(saved["status"], "failed")
        self.assertEqual(saved["error_code"], "QUERY_TIMEOUT")
        second, created = self.begin("new-request")
        self.assertTrue(created)
        self.storage.finish_query(first["query_id"], "succeeded", result={"late": True})
        conversation = self.storage.get_conversation(self.conversation["id"], self.user["id"])
        self.assertEqual(conversation["active_query_id"], second["query_id"])

    def test_independent_connections_compete_for_one_conversation(self):
        second = Storage(str(self.storage.engine.url))
        try:
            def submit(pair):
                storage, request = pair
                try:
                    return storage.begin_query(self.conversation["id"], self.user["id"], request, "并发问题")[1]
                except StorageConflict:
                    return False
            with ThreadPoolExecutor(max_workers=2) as pool:
                outcomes = list(pool.map(submit, [(self.storage, "race-a"), (second, "race-b")]))
            self.assertEqual(sum(outcomes), 1)
            self.assertEqual(len(self.storage.list_queries(self.conversation["id"])), 1)
        finally:
            second.engine.dispose()

    def test_feedback_requires_query_ownership(self):
        query, _ = self.begin()
        feedback = self.storage.add_feedback(query["query_id"], self.user["id"], "incorrect", "口径待复核")
        self.assertEqual(feedback["query_id"], query["query_id"])
        with self.assertRaises(StorageConflict):
            self.storage.add_feedback(query["query_id"], self.other["id"], "incorrect", "越权")
        self.assertEqual(len(self.storage.list_conversations(self.user["id"])), 1)
        self.assertEqual(self.storage.list_conversations(self.other["id"]), [])

    def test_concurrent_retries_share_one_query(self):
        second = Storage(str(self.storage.engine.url))
        try:
            def submit(storage):
                return storage.begin_query(self.conversation["id"], self.user["id"], "same-request", "重复问题")
            with ThreadPoolExecutor(max_workers=2) as pool:
                outcomes = list(pool.map(submit, [self.storage, second]))
            self.assertEqual(sum(created for _, created in outcomes), 1)
            self.assertEqual(len({query["query_id"] for query, _ in outcomes}), 1)
        finally:
            second.engine.dispose()

    def test_large_snapshot_is_explicitly_unavailable(self):
        query, _ = self.begin()
        self.storage.finish_query(query["query_id"], "succeeded", result={"data": "大" * 400000})
        saved = self.storage.get_query(query["query_id"])
        self.assertFalse(saved["result_snapshot_available"])
        self.assertIsNone(saved["result"])


if __name__ == "__main__":
    unittest.main()
