"""Tests for regex parsers in app.services.bot_ws_client."""

from datetime import datetime

from app.services.bot_ws_client import (
    parse_create_command,
    parse_complete_command,
    parse_file_list_command,
    parse_associate_command,
    is_cleanup_command,
    is_cleanup_confirm,
)


class TestParseCreateCommand:
    def test_valid(self):
        result = parse_create_command("创建 数据库实验 @2026-06-15 23:59 难度7 重要度8")
        assert result is not None
        assert result["title"] == "数据库实验"
        assert result["difficulty"] == 7.0
        assert result["importance"] == 8.0
        assert result["ddl_time"] == datetime(2026, 6, 15, 23, 59)

    def test_valid_minimal(self):
        result = parse_create_command("创建 测试 @2026-01-01 00:00 难度1 重要度1")
        assert result is not None
        assert result["title"] == "测试"

    def test_missing_difficulty(self):
        result = parse_create_command("创建 数据库实验 @2026-06-15 23:59 重要度8")
        assert result is None

    def test_wrong_format(self):
        assert parse_create_command("随便聊聊") is None
        assert parse_create_command("列表") is None
        assert parse_create_command("") is None

    def test_missing_at_sign(self):
        result = parse_create_command("创建 数据库实验 2026-06-15 23:59 难度7 重要度8")
        assert result is None


class TestParseCompleteCommand:
    def test_numeric_id(self):
        assert parse_complete_command("完成 3") == 3

    def test_short_code(self):
        assert parse_complete_command("完成 #DB-LAB4") == "DB-LAB4"

    def test_short_code_no_hash(self):
        assert parse_complete_command("完成 DB-LAB4") == "DB-LAB4"

    def test_invalid(self):
        assert parse_complete_command("完成") is None
        assert parse_complete_command("随便") is None


class TestParseFileListCommand:
    def test_valid(self):
        assert parse_file_list_command("文件 3") == 3

    def test_no_id(self):
        assert parse_file_list_command("文件") is None

    def test_non_numeric(self):
        assert parse_file_list_command("文件 abc") is None


class TestParseAssociateCommand:
    def test_valid(self):
        assert parse_associate_command("关联 1 2") == (1, 2)

    def test_missing_one(self):
        assert parse_associate_command("关联 1") is None

    def test_non_numeric(self):
        assert parse_associate_command("关联 a b") is None


class TestCleanupCommands:
    def test_cleanup(self):
        assert is_cleanup_command("清理已完成") is True
        assert is_cleanup_command("清理") is True
        assert is_cleanup_command("别的") is False

    def test_cleanup_confirm(self):
        assert is_cleanup_confirm("确认清理") is True
        assert is_cleanup_confirm("取消") is False
