# -*- coding: utf-8 -*-
"""正文查找替换工具

对书籍的 TXT / EPUB 格式执行正文级字符串替换（支持普通文本与正则两种模式），
以「生成新书」模式入库，原书零改动。

- **TXT**：检测编码 → str 层替换 → 原编码写回 → 新书入库；
- **EPUB**：zipfile 遍历（container → OPF → xhtml 条目）逐文件 str 替换，
  未修改条目字节原样保留，mimetype 置首且 ZIP_STORED 规范重写 → 新书入库。

对外接口：
- :meth:`preview` 同步返回匹配数 + 上下文样本 + 正则错误；
- :meth:`run` 后台执行替换并入库。

@author: 黏菌, 2026
"""
import logging
import os
import re
import threading
import time
import traceback
import zipfile
from typing import Callable, List, Optional, Tuple

from webserver.i18n import _
from webserver.services import AsyncService
from webserver.services.background_service import BackgroundService, BackgroundTask
from webserver.toolbox.base_tool import BaseTool

from . import book_utils
from . import encoding_detect

# EPUB 文本条目（正文）的 media-type
_TEXT_MEDIA_TYPES = ("application/xhtml+xml", "text/html")
_ITEM_RE = re.compile(r"<item\b[^>]*?>", re.IGNORECASE)
_ITEM_HREF_RE = re.compile(r'href\s*=\s*"([^"]+)"', re.IGNORECASE)
_ITEM_MT_RE = re.compile(r'media-type\s*=\s*"([^"]+)"', re.IGNORECASE)

SAMPLE_CTX = 50   # 预览上下文（前后各 N 字符）
SAMPLE_MAX = 5    # 预览样本条数上限


class TextReplaceTool(BaseTool):
    """对指定书籍的 TXT / EPUB 格式执行正文查找替换。"""

    service_item_name = "正文查找替换"

    _run_lock = threading.Lock()
    _last_task_id: Optional[int] = None

    @classmethod
    def is_running(cls) -> bool:
        task = cls.get_last_task()
        return bool(task and task.get("status") == BackgroundTask.STATUS_RUNNING)

    @classmethod
    def get_last_task(cls) -> Optional[dict]:
        if cls._last_task_id is None:
            return None
        return BackgroundService().get_task(cls._last_task_id)

    @staticmethod
    def info() -> dict:
        return {
            "tool_id": "text_replace",
            "name": "正文查找替换",
            "description": "对 TXT / EPUB 正文执行查找替换（支持正则），生成新书",
            "revision": "0.1.0",
            "author": "黏菌",
            "publish_date": "2026-08-09",
        }

    # ------------------------------------------------------------------ 工具

    @staticmethod
    def _compile(pattern: str, replacement: str, use_regex: bool):
        """编译替换规则。返回 (apply_fn, regex_error)。

        ``apply_fn(text) -> (new_text, count)``；编译失败时 apply_fn 为 None，
        错误信息写入 ``regex_error``。
        """
        if not pattern:
            return None, _("查找内容不能为空")
        if use_regex:
            try:
                rx = re.compile(pattern)
            except re.error as err:
                return None, _("正则表达式错误：%s") % err
            return (lambda text: rx.subn(replacement, text)), None
        return (lambda text: (text.replace(pattern, replacement),
                              text.count(pattern))), None

    @staticmethod
    def _collect_samples(text: str, pattern: str, use_regex: bool) -> List[dict]:
        """收集匹配上下文样本，用于预览。"""
        samples: List[dict] = []
        if use_regex:
            matches = list(re.finditer(pattern, text))
        else:
            matches = []
            start = 0
            while len(matches) < SAMPLE_MAX:
                idx = text.find(pattern, start)
                if idx < 0:
                    break
                matches.append(idx)
                start = idx + max(1, len(pattern))
        for m in matches[:SAMPLE_MAX]:
            idx = m.start() if use_regex else m
            length = (m.end() - m.start()) if use_regex else len(pattern)
            lo, hi = max(0, idx - SAMPLE_CTX), min(len(text), idx + length + SAMPLE_CTX)
            samples.append({
                "index": idx,
                "before": text[lo:hi],
                "matched": text[idx:idx + length],
            })
        return samples

    # ------------------------------------------------------------ 预览（同步）

    def preview(self, book_id: int, pattern: str, replacement: str, use_regex: bool) -> dict:
        """同步返回匹配数 + 上下文样本 + 正则错误。

        :param book_id:    Calibre 书籍 ID。
        :param pattern:    查找内容（普通文本或正则表达式）。
        :param replacement: 替换内容。
        :param use_regex:  是否按正则解析 pattern。
        :return dict: ``format``（TXT / EPUB）/ ``matches`` / ``samples`` /
            ``regex_error``。
        :raises RuntimeError: 书籍不存在 / 无 TXT、EPUB 格式 / 文件缺失。
        """
        apply_fn, regex_error = self._compile(pattern, replacement, use_regex)
        if apply_fn is None:
            return {"format": None, "matches": 0, "samples": [], "regex_error": regex_error}

        fmt, text = self._load_text(book_id)
        _, count = apply_fn(text)
        samples = self._collect_samples(text, pattern, use_regex)
        return {"format": fmt, "matches": count, "samples": samples, "regex_error": None}

    # ------------------------------------------------------------- 后台执行

    @AsyncService.register_service
    def run(self, book_id: int, pattern: str, replacement: str,
            use_regex: bool, suffix: str, user_id: int) -> None:
        """后台执行查找替换并生成新书。

        :param book_id:    Calibre 书籍 ID。
        :param pattern:    查找内容（普通文本或正则表达式）。
        :param replacement: 替换内容。
        :param use_regex:  是否按正则解析 pattern。
        :param suffix:     新书标题后缀（如「正文替换版」）。
        :param user_id:    操作用户 ID。
        """
        if not TextReplaceTool._run_lock.acquire(blocking=False):
            logging.warning(
                "[TextReplaceTool] Already running, skipping run for book_id=%d [uid:%d]",
                book_id, user_id,
            )
            return

        task_id = self.create_task(progress_data={"status": "starting", "book_id": book_id})
        TextReplaceTool._last_task_id = task_id
        progress_callback = self.make_progress_callback(task_id)
        error_message = None
        book_title = "Unknown"

        try:
            apply_fn, regex_error = self._compile(pattern, replacement, use_regex)
            if apply_fn is None:
                error_message = regex_error
                logging.error("[TextReplaceTool] Bad rule: %s [uid:%d]", regex_error, user_id)
                return

            books = self.db.get_data_as_dict(ids=[book_id])
            if not books:
                error_message = _("书籍不存在：ID=%d") % book_id
                logging.error("[TextReplaceTool] Book not found: ID=%d [uid:%d]", book_id, user_id)
                return
            book = books[0]
            book_title = book.get("title", "Unknown")

            self.update_task_progress(task_id, 10, {"status": "running", "stage": "reading"})
            progress_callback(10)

            fmt = self._detect_format(book)
            if fmt is None:
                error_message = _("该书籍没有 TXT 或 EPUB 格式，无法执行替换")
                logging.error("[TextReplaceTool] No TXT/EPUB format for book_id=%d [uid:%d]", book_id, user_id)
                return

            work_dir = self.get_work_dir(str(book_id))
            out_path = os.path.join(work_dir, "replaced_%d.%s" % (int(time.time()), fmt.lower()))

            self.update_task_progress(task_id, 30, {"status": "running", "stage": "processing"})
            progress_callback(30)

            if fmt == "TXT":
                count = self._replace_txt(book_id, apply_fn, out_path)
            else:
                count = self._replace_epub(book_id, apply_fn, out_path)

            self.update_task_progress(task_id, 80, {"status": "running", "stage": "saving"})
            progress_callback(80)

            new_book_id = book_utils.import_as_new_book(
                self, book_id, out_path, suffix or _("「正文替换版」"), user_id,
            )
            logging.info(
                "[TextReplaceTool] Replaced %s book_id=%d (%d hits) -> new book_id=%d [uid:%d]",
                fmt, book_id, count, new_book_id, user_id,
            )
            self.cleanup_work_dir(work_dir)

            self.add_msg(
                user_id, "success",
                _(u"书籍 [%s] 正文替换成功！命中 %d 处，已生成新书") % (book_title, count),
            )

        except Exception as err:
            error_message = str(err)
            self.add_msg(user_id, "danger", _(u"书籍 [%s] 正文替换失败！") % book_title)
            logging.error("[TextReplaceTool] Unexpected error for book_id=%d: %s", book_id, err)
            logging.error(traceback.format_exc())
        finally:
            self.complete_task(task_id, error_message=error_message)
            if error_message is None:
                self.update_task_progress(task_id, 100, {"status": "completed", "book_id": book_id})
            TextReplaceTool._run_lock.release()

    # ------------------------------------------------------------ 内部实现

    @staticmethod
    def _detect_format(book: dict) -> Optional[str]:
        """确定可用格式：TXT 优先，其次 EPUB；无则返回 None。"""
        fmts = [f.upper() for f in (book.get("available_formats") or [])]
        for fmt in ("TXT", "EPUB"):
            if fmt in fmts:
                return fmt
        return None

    def _load_text(self, book_id: int) -> Tuple[str, str]:
        """读取书籍 TXT / EPUB 正文并返回 (fmt, 拼接文本)。TXT 优先。"""
        books = self.db.get_data_as_dict(ids=[book_id])
        book = books[0] if books else {}
        fmts = [f.upper() for f in (book.get("available_formats") or [])]
        if "TXT" in fmts:
            txt_path = book_utils.get_book_file(self, book_id, "TXT")
            with open(txt_path, "rb") as f:
                data = f.read()
            text, _ = encoding_detect.decode_with_report(data)
            return "TXT", text
        if "EPUB" in fmts:
            epub_path = book_utils.get_book_file(self, book_id, "EPUB")
            entries = _read_zip_entries(epub_path)
            text = "\n".join(_decode_entry(entries[name]) for name in _find_text_entries(entries))
            return "EPUB", text
        raise RuntimeError(_("该书籍没有 TXT 或 EPUB 格式，无法执行替换"))

    def _replace_txt(self, book_id: int, apply_fn: Callable, out_path: str) -> int:
        """TXT：检测编码 → str 替换 → 原编码写回。返回命中数。"""
        txt_path = book_utils.get_book_file(self, book_id, "TXT")
        with open(txt_path, "rb") as f:
            data = f.read()
        text, report = encoding_detect.decode_with_report(data)
        new_text, count = apply_fn(text)
        with open(out_path, "wb") as f:
            f.write(new_text.encode(report["encoding"]))
        return count

    def _replace_epub(self, book_id: int, apply_fn: Callable, out_path: str) -> int:
        """EPUB：container → OPF → xhtml 条目逐文件替换，规范重写 zip。返回命中数。"""
        epub_path = book_utils.get_book_file(self, book_id, "EPUB")
        entries = _read_zip_entries(epub_path)
        total = 0
        for name in _find_text_entries(entries):
            text = _decode_entry(entries[name])
            new_text, count = apply_fn(text)
            if count > 0:
                entries[name] = new_text.encode("utf-8")
                total += count
        _write_zip(entries, out_path)
        return total


# --------------------------------------------------------------------- EPUB 助手


def _read_zip_entries(path: str) -> dict:
    """读取 zip 全部条目（跳过目录项），返回 {name: bytes}。"""
    entries = {}
    with zipfile.ZipFile(path, "r") as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            entries[info.filename] = zf.read(info.filename)
    return entries


def _decode_entry(data: bytes) -> str:
    """解码 EPUB 文本条目：UTF-8 优先，失败则用检测器兜底。"""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        text, _ = encoding_detect.decode_with_report(data)
        return text


def _find_text_entries(entries: dict) -> List[str]:
    """按 container.xml → OPF 的 manifest 定位正文（xhtml/html）条目名。"""
    container = entries.get("META-INF/container.xml")
    if not container:
        return []
    opf_path = _opf_path_from_container(container.decode("utf-8", errors="replace"))
    if not opf_path or opf_path not in entries:
        return []
    opf_text = _decode_entry(entries[opf_path])
    names = []
    base_dir = opf_path.rsplit("/", 1)[0] if "/" in opf_path else ""
    for tag in _ITEM_RE.findall(opf_text):
        mt = _ITEM_MT_RE.search(tag)
        href = _ITEM_HREF_RE.search(tag)
        if not mt or not href:
            continue
        if mt.group(1).lower() not in _TEXT_MEDIA_TYPES:
            continue
        href = href.group(1)
        if href.startswith("/"):
            href = href.lstrip("/")
        elif base_dir:
            href = "%s/%s" % (base_dir, href)
        # 归一化路径（去 ./ 与 ../）
        parts = []
        for seg in href.replace("\\", "/").split("/"):
            if seg in ("", "."):
                continue
            if seg == "..":
                if parts:
                    parts.pop()
                continue
            parts.append(seg)
        name = "/".join(parts)
        if name in entries:
            names.append(name)
    return names


def _opf_path_from_container(container_text: str) -> Optional[str]:
    """从 container.xml 提取 OPF 路径（rootfile full-path）。"""
    m = re.search(r'full-path\s*=\s*"([^"]+)"', container_text, re.IGNORECASE)
    return m.group(1) if m else None


def _write_zip(entries: dict, out_path: str) -> None:
    """规范重写 zip：mimetype 置首且 ZIP_STORED，其余 DEFLATED。"""
    order = [k for k in entries if k != "mimetype"]
    with zipfile.ZipFile(out_path, "w") as zout:
        zout.writestr(zipfile.ZipInfo("mimetype"),
                      entries.get("mimetype", b"application/epub+zip"),
                      compress_type=zipfile.ZIP_STORED)
        for name in order:
            zout.writestr(name, entries[name], compress_type=zipfile.ZIP_DEFLATED)
