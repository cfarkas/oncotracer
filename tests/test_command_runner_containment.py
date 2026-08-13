#!/usr/bin/env python3
"""Containment and validator regression tests for CommandRunner."""

from __future__ import annotations

import io
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from oncotracer_cli.runtime import CommandRunner, OncoTracerError


class CommandRunnerContainmentTests(unittest.TestCase):
    def test_environment_precedence_and_unset(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner = CommandRunner(
                root / "trace.tsv",
                echo=False,
                environment={"ORDER": "base", "REMOVE_BASE": "base"},
                protected_environment={
                    "ORDER": "protected",
                    "REMOVE_CALL": None,
                },
            )
            with patch.dict(
                os.environ,
                {"ORDER": "ambient", "REMOVE_CALL": "ambient"},
                clear=False,
            ):
                environment = runner.child_environment(
                    {
                        "ORDER": "call",
                        "REMOVE_CALL": "call",
                        "REMOVE_BASE": None,
                    },
                    containment={"ORDER": "contained", "FINAL_UNSET": None},
                )
            self.assertEqual(environment["ORDER"], "contained")
            self.assertNotIn("REMOVE_CALL", environment)
            self.assertNotIn("REMOVE_BASE", environment)
            self.assertNotIn("FINAL_UNSET", environment)
            self.assertEqual(environment["PYTHONDONTWRITEBYTECODE"], "1")

    def test_validator_brackets_success_and_nonzero_run(self) -> None:
        for returncode, check in ((0, True), (7, True), (7, False)):
            with (
                self.subTest(returncode=returncode, check=check),
                tempfile.TemporaryDirectory() as directory,
            ):
                events: list[str] = []
                runner = CommandRunner(
                    Path(directory) / "trace.tsv",
                    echo=False,
                    validators=(lambda: events.append("validate"),),
                )

                def execute(*_args, **_kwargs):
                    events.append("child")
                    return subprocess.CompletedProcess([], returncode)

                with patch(
                    "oncotracer_cli.runtime.subprocess.run", side_effect=execute
                ):
                    if returncode and check:
                        with self.assertRaises(OncoTracerError):
                            runner.run("test", ["example"], check=check)
                    else:
                        runner.run("test", ["example"], check=check)
                self.assertEqual(events, ["validate", "child", "validate"])

    def test_trace_record_failure_cannot_skip_post_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            events: list[str] = []
            runner = CommandRunner(
                Path(directory) / "trace.tsv",
                echo=False,
                validators=(lambda: events.append("validate"),),
            )
            with (
                patch(
                    "oncotracer_cli.runtime.subprocess.run",
                    side_effect=lambda *_args, **_kwargs: (
                        events.append("child") or subprocess.CompletedProcess([], 0)
                    ),
                ),
                patch.object(runner, "_record", side_effect=OSError("trace changed")),
                self.assertRaisesRegex(OSError, "trace changed"),
            ):
                runner.run("test", ["example"])
            self.assertEqual(events, ["validate", "child", "validate"])

    def test_pipeline_reaps_both_children_before_post_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            events: list[str] = []
            runner = CommandRunner(
                Path(directory) / "trace.tsv",
                echo=False,
                validators=(lambda: events.append("validate"),),
            )
            left = MagicMock()
            right = MagicMock()
            left.stdout = io.BytesIO()
            left.returncode = 0
            right.returncode = 0
            left.wait.return_value = 0
            right.wait.return_value = 0
            left.poll.return_value = 0
            right.poll.return_value = 0

            def spawn(*_args, **_kwargs):
                events.append("spawn")
                return left if events.count("spawn") == 1 else right

            def validate():
                events.append("validate")
                if events.count("validate") == 2:
                    self.assertGreaterEqual(left.wait.call_count, 1)
                    self.assertGreaterEqual(right.wait.call_count, 1)

            runner.validators = (validate,)
            with patch("oncotracer_cli.runtime.subprocess.Popen", side_effect=spawn):
                runner.pipeline("pipeline", ["left"], ["right"])
            self.assertEqual(events[0], "validate")
            self.assertEqual(events[-1], "validate")

    def test_pipeline_second_spawn_failure_reaps_left_before_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            events: list[str] = []
            runner = CommandRunner(Path(directory) / "trace.tsv", echo=False)
            left = MagicMock()
            left.stdout = io.BytesIO()
            left.returncode = -15
            left.poll.side_effect = (None, -15)
            left.wait.return_value = -15

            def validate():
                events.append("validate")
                if len(events) == 2:
                    self.assertTrue(left.stdout.closed)
                    self.assertTrue(left.terminate.called or left.wait.called)

            runner.validators = (validate,)
            with (
                patch(
                    "oncotracer_cli.runtime.subprocess.Popen",
                    side_effect=(left, OSError("second spawn failed")),
                ),
                self.assertRaisesRegex(OSError, "second spawn failed"),
            ):
                runner.pipeline("pipeline", ["left"], ["right"])
            self.assertEqual(events, ["validate", "validate"])

    def test_pipeline_wait_failure_cannot_skip_post_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            events: list[str] = []
            runner = CommandRunner(
                Path(directory) / "trace.tsv",
                echo=False,
                validators=(lambda: events.append("validate"),),
            )
            left = MagicMock()
            right = MagicMock()
            left.stdout = io.BytesIO()
            left.poll.return_value = 0
            right.poll.return_value = 0
            right.wait.side_effect = OSError("wait failed")
            with (
                patch(
                    "oncotracer_cli.runtime.subprocess.Popen",
                    side_effect=(left, right),
                ),
                self.assertRaisesRegex(OSError, "wait failed"),
            ):
                runner.pipeline("pipeline", ["left"], ["right"])
            self.assertEqual(events, ["validate", "validate"])


if __name__ == "__main__":
    unittest.main()
