# 正文查找替换（MyBooks Toolbox 插件）

> 工具 ID：`text_replace`　作者：黏菌　版本：0.1.0
> 书库 → 工具箱 → 正文查找替换 → 选择书籍 → 预览 → 执行替换

## 功能

对书籍 **TXT / EPUB** 格式的正文执行字符串查找替换（普通文本 / 正则两种模式），
结果**另存为新书**（原书零改动）：

- **TXT**：检测编码 → str 层替换 → **原编码写回**（UTF-16/32 按 BOM 字节序并
  保留 BOM，BE 文件不会翻转成 LE）→ 新书入库；原编码无法表示的替换内容
  （如 BIG5 遇简体字）自动降级 UTF-8 并同步改写 XML 声明；
- **EPUB**：zipfile 遍历（`META-INF/container.xml` → OPF manifest → xhtml 正文条目）
  逐文件替换；container / OPF 按规范 XML 解析（ElementTree 优先，非良构时正则
  兜底，属性单双引号均支持），href 按 URI unquote 后比对，文件名含空格
  （`%20`）可正常定位；**未修改条目字节原样保留**；按 EPUB 规范重写
  （`mimetype` 置首且 `ZIP_STORED`）→ 新书入库；
- **未命中保护**：查找内容 0 命中时不生成新书（避免入库内容相同的副本），
  任务正常结束并给出提示；
- **预览**：同步返回命中数 + 上下文样本（pre / match / post 三段直出高亮）+
  正则错误提示；超过 `PREVIEW_LIMIT`（20 万字符）只统计前缀并置 `truncated`；
- **格式选择**：可显式指定 TXT / EPUB，默认 **EPUB 优先**（其次 TXT）；
- 新书标题默认追加「正文替换版」后缀，可自定义。

## API

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/toolbox/text_replace/preview` | 同步 `{book_id, pattern, replacement, use_regex, format?}` → `format / matches / samples / regex_error / truncated` |
| POST | `/api/toolbox/text_replace/run` | 后台执行 `{book_id, pattern, replacement, use_regex, suffix, format?}` → 新书入库 |
| GET | `/api/toolbox/text_replace/progress` | 轮询 `status / progress / stage` |

## 文件清单

> 本仓库与 **mybooks v4.4.1（47354337）** 全量对齐：下列「覆盖」文件与 v4.4.1
> 宿主自带内容一致，宿主为 v4.4.1 时覆盖等于无操作；其余为新增文件。

```
text-replace/
├── README.md
├── webserver/
│   ├── handlers/toolbox.py            # 覆盖 = v4.4.1 原样（含全部工具 handler + text_replace 3 路由）
│   └── toolbox/
│       ├── toolset.py                 # 覆盖 = v4.4.1 原样（含全部工具注册）
│       ├── text_replace.py            # 插件主体（Tool 类 + EPUB zip 助手）
│       └── utils/
│           ├── encoding_detect.py     # 公共模块①：编码检测（与 txt_encoding_fixer 共享）
│           └── book_utils.py          # 公共模块②：get_book_file / import_as_new_book
├── app/
│   ├── src/pages/toolbox/text_replace.vue           # Vue 2.6 + Vuetify 2 页面
│   └── locales/{en,zh,zh-TW}.json     # 覆盖 = v4.4.1 原样（含 textReplace 等全部词条）
└── tests/                             # standalone 单测（text_replace 32 + encoding_detect 53）
    ├── test_text_replace_core.py
    └── test_encoding_detect.py
```

## 安装部署

将以下文件复制到 mybooks 源码对应位置：

| 源文件 | 目标位置 |
|--------|----------|
| `webserver/toolbox/text_replace.py` | `webserver/toolbox/` |
| `webserver/toolbox/utils/encoding_detect.py` | `webserver/toolbox/utils/` |
| `webserver/toolbox/utils/book_utils.py` | `webserver/toolbox/utils/` |
| `webserver/toolbox/toolset.py` | **覆盖** `webserver/toolbox/toolset.py` |
| `webserver/handlers/toolbox.py` | **覆盖** `webserver/handlers/toolbox.py` |
| `app/src/pages/toolbox/text_replace.vue` | `app/src/pages/toolbox/` |
| `app/locales/en.json` / `zh.json` / `zh-TW.json` | **覆盖** `app/locales/` 同名文件 |

> **宿主版本注意**：`toolset.py` / `handlers/toolbox.py` / locales 为 v4.4.1 全量
> 文件，会无条件 import v4.4.1 工具箱的全部插件（含 `TxtEncodingFixerTool` 等）。
> 宿主为 **v4.4.1** 时直接覆盖即可；宿主**低于该版本**时，请删除修改版中宿主缺失
> 工具的引用（toolset.py 的 import + register、handlers/toolbox.py 的 import +
> 对应 handler 类 + 路由），或优先升级宿主。

### 与「TXT编码修复」插件的关系

两个插件共享 `utils/encoding_detect.py` / `utils/book_utils.py`。同时安装时，
将「TXT编码修复」包中的 `txt_encoding_fixer.py` 复制到 `webserver/toolbox/`
即可，无需手工合并（两包的共享模块与覆盖文件同源）。

### 依赖

- `chardet`（`requirements.txt` 已包含，v7.x）：TXT 编码检测，缺失时自动退化为纯规则检测。

## 运行测试

```bash
python -m unittest discover -s tests -v
# 或：python -m pytest tests/test_text_replace_core.py -q
```

覆盖：普通 / 正则（含 `\1` 分组引用）/ 非法正则 / 空 pattern 的错误处理、
上下文样本收集与全文命中统计、格式选择（显式指定 / EPUB 优先）、
TXT GB18030 原编码写回、UTF-16LE/BE 与 UTF-32BE 按 BOM 字节序写回（BOM 保持）、
EPUB 正文条目定位（container→OPF→xhtml；单引号属性 / `%20` 编码 href /
非良构 OPF 正则回退 / fragment 与根路径归一化）、逐文件替换 +
mimetype 首条 `ZIP_STORED` 规范断言、未修改文件字节保留、
条目解码 UTF-8 失败兜底、原编码无法表示时降级 UTF-8 并同步 XML 声明、
run() 命中 0 处不生成新书。

> 设计边界：替换是纯文本层的 str 替换，若替换内容本身含 `<` / `&` / `>`
> 等标记字符，产出的 XHTML 可能不再良构（例如想插入 `<br/>` 属于刻意用法，
> 工具不拦截也不校验）——对替换结果有洁癖的请在预览确认后使用。

## 测试库实测步骤

1. 准备一本 TXT 或 EPUB 书（如书名含「机器学习」）入库；
2. 书库 → 工具箱 → 正文查找替换 → 搜索并选择该书；
3. 普通模式：查找「机器学习」、替换「AI」；点「预览」应显示命中数与上下文样本；
4. 切到正则模式试 `第\d+章` → `第X章`，确认小抄提示与预览正常；
5. 点「执行替换」：右上角消息通知「正文替换成功！命中 N 处」，书库出现标题带
   「正文替换版」后缀的新书；
6. 阅读新书确认替换生效：TXT 编码保持原编码（BOM 不变）、EPUB 排版与图片正常
   （`mimetype` 规范重写保证阅读器兼容）；原书文件未被改动。
