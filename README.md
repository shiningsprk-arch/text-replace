# 正文查找替换（MyBooks Toolbox 插件）

> 工具 ID：`text_replace`　作者：黏菌　版本：0.1.0
> 书库 → 工具箱 → 正文查找替换 → 选择书籍 → 预览 → 执行替换

## 功能

对书籍 **TXT / EPUB** 格式的正文执行字符串查找替换（普通文本 / 正则两种模式），
结果**另存为新书**（原书零改动）：

- **TXT**：检测编码 → str 层替换 → **原编码写回**（BOM 保持）→ 新书入库；
- **EPUB**：zipfile 遍历（`META-INF/container.xml` → OPF manifest → xhtml 正文条目）
  逐文件替换；**未修改条目字节原样保留**；按 EPUB 规范重写
  （`mimetype` 置首且 `ZIP_STORED`）→ 新书入库；
- **预览**：同步返回命中数 + 上下文样本（含命中段高亮位置）+ 正则错误提示；
- 新书标题默认追加「正文替换版」后缀，可自定义。

## API

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/toolbox/text_replace/preview` | 同步 `{book_id, pattern, replacement, use_regex}` → `format / matches / samples / regex_error` |
| POST | `/api/toolbox/text_replace/run` | 后台执行 `{book_id, pattern, replacement, use_regex, suffix}` → 新书入库 |
| GET | `/api/toolbox/text_replace/progress` | 轮询 `status / progress / stage` |

## 文件清单

```
正文查找替换/
├── README.md
├── webserver/
│   ├── handlers/toolbox.py            # 修改版：+3 handler（preview/run/progress）+3 路由
│   └── toolbox/
│       ├── toolset.py                 # 修改版：+2 import +2 register（含 txt_encoding_fixer，见下）
│       ├── text_replace.py            # 插件主体（Tool 类 + EPUB zip 助手）
│       ├── encoding_detect.py         # 公共模块①：编码检测（与 txt_encoding_fixer 共享）
│       └── book_utils.py              # 公共模块②：get_book_file / import_as_new_book
├── app/
│   ├── src/pages/toolbox/text_replace.vue           # Vue 2.6 + Vuetify 2 页面
│   └── locales/{en,zh,zh-TW}.json     # 修改版：+textReplace 块（另含 txtEncodingFixer 块，见下）
└── tests/test_text_replace_core.py    # standalone 单测（11 个）
```

## 安装部署（4 处修改）

将以下文件复制到 mybooks 源码对应位置：

| 源文件 | 目标位置 |
|--------|----------|
| `webserver/toolbox/text_replace.py` | `webserver/toolbox/` |
| `webserver/toolbox/encoding_detect.py` | `webserver/toolbox/` |
| `webserver/toolbox/book_utils.py` | `webserver/toolbox/` |
| `webserver/toolbox/toolset.py` | **覆盖** `webserver/toolbox/toolset.py` |
| `webserver/handlers/toolbox.py` | **覆盖** `webserver/handlers/toolbox.py` |
| `app/src/pages/toolbox/text_replace.vue` | `app/src/pages/toolbox/` |
| `app/locales/en.json` / `zh.json` / `zh-TW.json` | **覆盖** `app/locales/` 同名文件 |

### 与「TXT编码修复」插件的关系

两个插件共享 `encoding_detect.py` / `book_utils.py`，且本文件夹的修改版
`toolset.py` 与 `handlers/toolbox.py` **已同时包含两个插件的注册与路由**
（6 handler + 6 路由），locales 亦同时含 `textReplace` + `txtEncodingFixer` 两块：

- 只装本插件：直接按上表复制即可（多余的另一插件注册行无害——见下方注意）；
- 同时装两个：将 `TXT编码修复` 文件夹中的 `txt_encoding_fixer.py` 一并复制即可，
  **两个文件夹的修改版文件内容一致，任取其一**，无需手工合并。

> 注意：本文件夹 `toolset.py` / `handlers/toolbox.py` 会 import
> `TxtEncodingFixerTool`。若**只装本插件**，请删除修改版中的这两处引用
> （`toolset.py` 的 import + register 各 1 行；`toolbox.py` 的 import 1 行 +
> `AdminTxtEncodingFixer*` 3 个 handler + 3 条路由），或直接改用
> `TXT编码修复` 文件夹中同名的修改版（内容一致，反向删 `TextReplaceTool` 引用）。

### 依赖

- `chardet`（`requirements.txt` 已包含，v7.x）：TXT 编码检测，缺失时自动退化为纯规则检测。

## 运行测试

```bash
python -m unittest discover -s tests -v
# 或：python tests/test_text_replace_core.py
```

覆盖：普通 / 正则（含 `\1` 分组引用）/ 非法正则 / 空 pattern 的错误处理、
上下文样本收集、TXT GB18030 原编码写回、EPUB 正文条目定位（container→OPF→xhtml）、
逐文件替换 + mimetype 首条 `ZIP_STORED` 规范断言、未修改文件字节保留、
条目解码 UTF-8 失败兜底。

## 测试库实测步骤

1. 准备一本 TXT 或 EPUB 书（如书名含「机器学习」）入库；
2. 书库 → 工具箱 → 正文查找替换 → 搜索并选择该书；
3. 普通模式：查找「机器学习」、替换「AI」；点「预览」应显示命中数与上下文样本；
4. 切到正则模式试 `第\d+章` → `第X章`，确认小抄提示与预览正常；
5. 点「执行替换」：右上角消息通知「正文替换成功！命中 N 处」，书库出现标题带
   「正文替换版」后缀的新书；
6. 阅读新书确认替换生效：TXT 编码保持原编码（BOM 不变）、EPUB 排版与图片正常
   （`mimetype` 规范重写保证阅读器兼容）；原书文件未被改动。
