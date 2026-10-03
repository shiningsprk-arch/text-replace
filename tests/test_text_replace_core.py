# -*- coding: utf-8 -*-
"""text_replace 核心单元测试（standalone，stub 掉 webserver / calibre 依赖）。

覆盖：替换规则编译、样本收集、TXT 原编码写回、EPUB 条目定位与规范重写。

运行：python -m unittest discover -s tests 或 python tests/test_text_replace_core.py
"""
import io
import os
import sys
import types
import unittest
import zipfile

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
TOOLBOX_DIR = os.path.join(TESTS_DIR, "..", "webserver", "toolbox")


def _stub_webserver():
    """注入最小 webserver / calibre 依赖，使 text_replace 可独立导入。"""
    webserver = types.ModuleType("webserver")
    toolbox = types.ModuleType("webserver.toolbox")
    toolbox.__path__ = [TOOLBOX_DIR]  # 相对导入找到真实 book_utils.py
    webserver.toolbox = toolbox

    i18n = types.ModuleType("webserver.i18n")
    i18n._ = lambda s: s
    webserver.i18n = i18n

    utils = types.ModuleType("webserver.utils")
    utils.super_strip = lambda s: (s or "").strip()
    utils.get_title_sort = lambda s: s
    webserver.utils = utils

    models = types.ModuleType("webserver.models")
    models.Item = type("Item", (), {"save": lambda self: None})
    webserver.models = models

    services = types.ModuleType("webserver.services")
    _register_service = staticmethod(lambda fn: fn)  # no-op 装饰器
    _register_function = staticmethod(lambda fn: fn)
    services.AsyncService = type("AsyncService", (), {
        "register_service": _register_service,
        "register_function": _register_function,
    })
    webserver.services = services

    bs = types.ModuleType("webserver.services.background_service")
    bs.BackgroundService = type("BackgroundService", (), {})
    bs.BackgroundTask = type("BackgroundTask", (), {
        "STATUS_RUNNING": "running",
        "STATUS_FAILED": "failed",
        "STATUS_COMPLETED": "completed",
    })
    webserver.services.background_service = bs

    base_tool = types.ModuleType("webserver.toolbox.base_tool")
    base_tool.BaseTool = type("BaseTool", (), {})
    toolbox.base_tool = base_tool

    calibre = types.ModuleType("calibre")
    ebooks = types.ModuleType("calibre.ebooks")
    metadata = types.ModuleType("calibre.ebooks.metadata")
    book = types.ModuleType("calibre.ebooks.metadata.book")
    base = types.ModuleType("calibre.ebooks.metadata.book.base")
    base.Metadata = type("Metadata", (), {})
    calibre.ebooks = ebooks
    calibre.ebooks.metadata = metadata
    calibre.ebooks.metadata.book = book
    calibre.ebooks.metadata.book.base = base

    sys.modules.update({
        "webserver": webserver,
        "webserver.toolbox": toolbox,
        "webserver.i18n": i18n,
        "webserver.utils": utils,
        "webserver.models": models,
        "webserver.services": services,
        "webserver.services.background_service": bs,
        "webserver.toolbox.base_tool": base_tool,
        "calibre": calibre,
        "calibre.ebooks": ebooks,
        "calibre.ebooks.metadata": metadata,
        "calibre.ebooks.metadata.book": book,
        "calibre.ebooks.metadata.book.base": base,
    })


_STUBBED_MODULE_NAMES = (
    "webserver",
    "webserver.toolbox",
    "webserver.i18n",
    "webserver.utils",
    "webserver.models",
    "webserver.services",
    "webserver.services.background_service",
    "webserver.toolbox.base_tool",
    "calibre",
    "calibre.ebooks",
    "calibre.ebooks.metadata",
    "calibre.ebooks.metadata.book",
    "calibre.ebooks.metadata.book.base",
)
# 记录 stub 之前 sys.modules 里已有的内容（通常是 None，即真实 webserver 包尚未被导入过），
# 导入完 text_replace 需要的符号后立即恢复，避免这份 stub（尤其是缺 book_id/Message 的假
# webserver.models）污染同一个 pytest 进程里在这之后收集到的其它测试文件。
_ORIGINAL_MODULES = {name: sys.modules.get(name) for name in _STUBBED_MODULE_NAMES}

_stub_webserver()

from webserver.toolbox.text_replace import (  # noqa: E402
    TextReplaceTool,
    _decode_entry,
    _encode_entry,
    _find_text_entries,
    _read_zip_entries,
    _read_text_entries,
    _write_zip,
)

for _name, _original in _ORIGINAL_MODULES.items():
    if _original is None:
        sys.modules.pop(_name, None)
    else:
        sys.modules[_name] = _original

GBK_TEXT = "第一章\u3000序章\n人工智能的发展历程，包括机器学习与深度学习。"
CH1 = "<html><body><p>第一章 人工智能的黎明</p><p>机器学习是核心。</p></body></html>"
CH2 = "<html><body><p>第二章 机器学习与深度学习</p><p>深度学习是机器学习的子集。</p></body></html>"

CONTAINER = (
    '<?xml version="1.0"?>'
    '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
    '<rootfiles><rootfile full-path="OEBPS/content.opf" '
    'media-type="application/oebps-package+xml"/></rootfiles></container>'
)
OPF = (
    '<?xml version="1.0"?>'
    '<package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
    '<manifest>'
    '<item id="c1" href="ch1.xhtml" media-type="application/xhtml+xml"/>'
    '<item id="c2" href="ch2.xhtml" media-type="application/xhtml+xml"/>'
    '<item id="css" href="style.css" media-type="text/css"/>'
    '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
    '</manifest></package>'
)
CSS = "body { font-family: serif; }"


def build_mini_epub(path):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", CONTAINER)
        zf.writestr("OEBPS/content.opf", OPF)
        zf.writestr("OEBPS/ch1.xhtml", CH1)
        zf.writestr("OEBPS/ch2.xhtml", CH2)
        zf.writestr("OEBPS/style.css", CSS)


class FakeDB:
    def __init__(self, fmts, path=None, paths=None):
        self.fmts = [fmts] if isinstance(fmts, str) else list(fmts)
        self.path = path
        self.paths = paths or {}

    def get_data_as_dict(self, ids=None):
        return [{"available_formats": list(self.fmts), "title": "test book"}]

    def format_abspath(self, book_id, fmt, index_is_id=False):
        if self.paths:
            return self.paths.get(fmt)
        return self.path


class FakeCalibreAPI:
    """镜像 CoreAPI.calibre 里 text_replace / book_utils 用到的那几个方法，转发给 FakeDB。"""

    def __init__(self, db):
        self._db = db

    def get_data_as_dict(self, ids=None):
        return self._db.get_data_as_dict(ids=ids)

    def format_abspath(self, book_id, fmt):
        return self._db.format_abspath(book_id, fmt, index_is_id=True)


class FakeAPI:
    def __init__(self, db):
        self.calibre = FakeCalibreAPI(db)


class TestCompile(unittest.TestCase):
    """替换规则编译。"""

    def test_plain(self):
        apply_fn, err = TextReplaceTool._compile("机器", "AI", False)
        self.assertIsNone(err)
        new_text, count = apply_fn("机器学习的机器核心")
        self.assertEqual(count, 2)
        self.assertEqual(new_text, "AI学习的AI核心")

    def test_regex_group(self):
        apply_fn, err = TextReplaceTool._compile(r"第(\d+)章", "Chapter \\1", True)
        self.assertIsNone(err)
        new_text, count = apply_fn("第1章 内容 第2章 内容")
        self.assertEqual(count, 2)
        self.assertEqual(new_text, "Chapter 1 内容 Chapter 2 内容")

    def test_regex_error(self):
        apply_fn, err = TextReplaceTool._compile("(unclosed", "x", True)
        self.assertIsNone(apply_fn)
        self.assertIsNotNone(err)

    def test_empty_pattern(self):
        apply_fn, err = TextReplaceTool._compile("", "x", False)
        self.assertIsNone(apply_fn)
        self.assertIsNotNone(err)


class TestSamples(unittest.TestCase):
    """上下文样本收集。"""

    def test_plain_samples(self):
        text = "aa 猫 aa 狗 aa"
        samples = TextReplaceTool._collect_samples(text, "aa", False)
        self.assertEqual(len(samples), 3)
        self.assertEqual(samples[0]["match"], "aa")
        self.assertIn("pre", samples[0])
        self.assertIn("post", samples[0])

    def test_regex_samples(self):
        text = "第1章 x 第2章 y 第3章 z"
        samples = TextReplaceTool._collect_samples(text, r"第\d章", True)
        self.assertEqual(len(samples), 3)
        self.assertEqual(samples[0]["match"], "第1章")
        self.assertEqual(samples[0]["index"], 0)

    def test_no_match(self):
        samples = TextReplaceTool._collect_samples("hello", "zzz", False)
        self.assertEqual(samples, [])

    def test_scan_samples_count_all(self):
        # 命中数统计必须覆盖全文（不只采样的 5 条）
        text = ("猫 " * 100)
        count, samples = TextReplaceTool._scan_samples(text, "猫", False)
        self.assertEqual(count, 100)
        self.assertEqual(len(samples), 5)


class TestFormatSelection(unittest.TestCase):
    """格式选择：显式指定 / 自动选择优先级。"""

    def test_detect_specified_txt(self):
        book = {"available_formats": ["TXT", "EPUB"]}
        self.assertEqual(TextReplaceTool._detect_format(book, "TXT"), "TXT")

    def test_detect_specified_epub_lower(self):
        book = {"available_formats": ["TXT", "EPUB"]}
        self.assertEqual(TextReplaceTool._detect_format(book, "epub"), "EPUB")

    def test_detect_specified_missing_raises(self):
        book = {"available_formats": ["EPUB"]}
        with self.assertRaises(RuntimeError):
            TextReplaceTool._detect_format(book, "TXT")

    def test_detect_default_epub_priority(self):
        book = {"available_formats": ["TXT", "EPUB"]}
        self.assertEqual(TextReplaceTool._detect_format(book), "EPUB")

    def test_detect_default_txt_fallback(self):
        book = {"available_formats": ["PDF", "TXT"]}
        self.assertEqual(TextReplaceTool._detect_format(book), "TXT")

    def test_detect_none(self):
        book = {"available_formats": ["PDF"]}
        self.assertIsNone(TextReplaceTool._detect_format(book))

    def test_load_texts_selects_format(self):
        epub = os.path.join(TESTS_DIR, "_tmp_sel.epub")
        txt = os.path.join(TESTS_DIR, "_tmp_sel.txt")
        build_mini_epub(epub)
        with io.open(txt, "wb") as f:
            f.write(GBK_TEXT.encode("gb18030"))
        try:
            tool = TextReplaceTool()
            tool.api = FakeAPI(FakeDB(["EPUB", "TXT"], paths={"EPUB": epub, "TXT": txt}))

            fmt, texts = tool._load_texts(0)  # 未指定：EPUB 优先
            self.assertEqual(fmt, "EPUB")
            self.assertEqual(len(texts), 2)
            self.assertIn("机器学习", texts[0])

            fmt, texts = tool._load_texts(0, "TXT")
            self.assertEqual(fmt, "TXT")
            self.assertEqual(len(texts), 1)
            self.assertIn("第一章", texts[0])

            with self.assertRaises(RuntimeError):
                tool._load_texts(0, "PDF")
        finally:
            os.remove(epub)
            os.remove(txt)


class TestTxtReplace(unittest.TestCase):
    """TXT：原编码写回。"""

    def test_gb18030_write_back(self):
        tmp = os.path.join(TESTS_DIR, "_tmp_gbk.txt")
        with io.open(tmp, "wb") as f:
            f.write(GBK_TEXT.encode("gb18030"))
        try:
            tool = TextReplaceTool()
            tool.api = FakeAPI(FakeDB("TXT", tmp))
            apply_fn, _ = TextReplaceTool._compile("人工智能", "AI", False)
            out = os.path.join(TESTS_DIR, "_tmp_out.txt")
            count = tool._replace_txt(0, apply_fn, out)
            self.assertEqual(count, 1)
            with io.open(out, "rb") as f:
                data = f.read()
            # 仍为 GB18030 编码，内容已替换
            self.assertEqual(data.decode("gb18030"),
                             GBK_TEXT.replace("人工智能", "AI"))
            os.remove(out)
        finally:
            os.remove(tmp)


class TestEpubReplace(unittest.TestCase):
    """EPUB：条目定位、逐文件替换、规范重写。"""

    def test_find_text_entries(self):
        tmp = os.path.join(TESTS_DIR, "_tmp.epub")
        build_mini_epub(tmp)
        try:
            entries = _read_zip_entries(tmp)
            names = _find_text_entries(entries)
            self.assertEqual(sorted(names), ["OEBPS/ch1.xhtml", "OEBPS/ch2.xhtml"])
        finally:
            os.remove(tmp)

    def test_read_text_entries_only_text(self):
        # 预览只读正文相关条目，不读取图片/字体等非文本条目
        tmp = os.path.join(TESTS_DIR, "_tmp.epub")
        build_mini_epub(tmp)
        try:
            entries = _read_text_entries(tmp)
            self.assertIn("META-INF/container.xml", entries)
            self.assertIn("OEBPS/content.opf", entries)
            self.assertIn("OEBPS/ch1.xhtml", entries)
            self.assertIn("OEBPS/ch2.xhtml", entries)
            self.assertNotIn("OEBPS/style.css", entries)  # 非文本条目不读
        finally:
            os.remove(tmp)

    def test_replace_and_rewrite(self):
        tmp = os.path.join(TESTS_DIR, "_tmp.epub")
        build_mini_epub(tmp)
        out = os.path.join(TESTS_DIR, "_tmp_out.epub")
        try:
            tool = TextReplaceTool()
            tool.api = FakeAPI(FakeDB("EPUB", tmp))
            apply_fn, _ = TextReplaceTool._compile("机器学习", "AI", False)
            count = tool._replace_epub(0, apply_fn, out)
            self.assertEqual(count, 3)  # ch1 1 处 + ch2 2 处

            with zipfile.ZipFile(out, "r") as zf:
                names = zf.namelist()
                # mimetype 必须为首条且 STORED（EPUB 规范）
                self.assertEqual(names[0], "mimetype")
                self.assertEqual(zf.getinfo("mimetype").compress_type, zipfile.ZIP_STORED)
                # 正文已替换
                ch1 = zf.read("OEBPS/ch1.xhtml").decode("utf-8")
                ch2 = zf.read("OEBPS/ch2.xhtml").decode("utf-8")
                self.assertIn("AI是核心", ch1)
                self.assertIn("AI与深度学习", ch2)
                self.assertNotIn("机器学习", ch1)
                # 未修改文件字节原样保留
                self.assertEqual(zf.read("OEBPS/style.css").decode("utf-8"), CSS)
        finally:
            os.remove(tmp)
            if os.path.exists(out):
                os.remove(out)

    def test_decode_entry_fallback(self):
        # UTF-8 解码失败时走检测器兜底，不抛异常
        text, enc = _decode_entry(b"\xe7\xac\xac\xe4\xb8\x80\xe7\xab\xa0\xff\xfe")
        self.assertTrue(text)
        self.assertTrue(enc)

    def test_encode_entry_fallback_utf8(self):
        # 原编码（BIG5）无法表示文本时降级 UTF-8，不抛异常
        raw = _encode_entry("机器𠀀", "big5")
        self.assertEqual(raw.decode("utf-8"), "机器𠀀")

    def test_encode_entry_xml_decl_synced(self):
        # 降级 UTF-8 时同步改写 XML 声明的 encoding
        text = '<?xml version="1.0" encoding="big5"?><html/>龙'
        raw = _encode_entry(text, "big5")
        out = raw.decode("utf-8")
        self.assertIn('encoding="utf-8"', out)
        self.assertNotIn("big5", out)


if __name__ == "__main__":
    unittest.main()
