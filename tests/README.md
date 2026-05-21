# 测试使用指南

本目录存放项目的单元测试与集成测试,使用 Python 内置的 `unittest` 框架编写,无需额外安装 `pytest` 也能跑。

---

## 1. 快速开始

每次开新终端都要做的两步:

```bash
cd /Users/mikaelsnow/Documents/ECNU/Paper_Comment_Poster/poster_agent_backend
source .venv312/bin/activate
```

激活之后,一句命令跑全部测试:

```bash
python -m unittest tests.test_svfp_loop_logger -v
```

末尾如果显示 `Ran 56 tests in 0.6s` 和 `OK`,就是全过。

---

## 2. 四种运行粒度

按从粗到细排列。改了某个具体功能时,只跑相关测试,几秒钟一轮,效率更高。

| 场景 | 命令 |
|---|---|
| 跑整个文件(全量回归) | `python -m unittest tests.test_svfp_loop_logger -v` |
| 只跑某个类 | `python -m unittest tests.test_svfp_loop_logger.RunArchiveTests -v` |
| 只跑某一个测试方法 | `python -m unittest tests.test_svfp_loop_logger.RunArchiveTests.test_demo_does_not_pollute_runs_root -v` |
| 跑 tests/ 下所有测试文件(以后扩展用) | `python -m unittest discover -s tests -v` |

**记忆规则:** 粒度靠 `.` 一层层往下钻 —— `模块.类.方法`;`-v` 是 verbose(详细)开关,不加就只输出点号汇总。

---

## 3. 测试文件当前包含的类

`test_svfp_loop_logger.py` 把 56 个测试分成 11 个 `TestCase` 类,按功能分组:

| 类名 | 验证目标 |
|---|---|
| `SVFPTests` | SVFP 反馈协议的枚举、构造、校验、Schema |
| `ConvergenceTests` | 迭代收敛检测器(分数阈值 / 最大轮数 / 停滞容忍) |
| `HistoryLoggerTests` | `feedback_history.json` 的读写、批量、UTF-8 |
| `EndToEndTests` | SVFP + 收敛 + 历史记录的完整链路 |
| `VlmParseRecoveryTests` | VLM 输出 JSON 的解析与容错(截断、非 JSON 文本) |
| `HeuristicCheckerTests` | 启发式布局检测器的触发逻辑 |
| `FeedbackApplierTests` | 反馈到布局动作的应用 |
| `PreviewRendererTests` | 预览图渲染的差异性(迭代水印、内容差异) |
| `EnhancedApplierTests` | 增强版反馈处理(字号、对比度、密度) |
| `RunArchiveTests` | run 归档目录的创建、保存、索引、隔离性 |

改了哪块功能,就跑对应的类。例如改了 `app/run_archive.py`,只需:

```bash
python -m unittest tests.test_svfp_loop_logger.RunArchiveTests -v
```

---

## 4. 怎么读输出

### 安静模式(不加 `-v`)

```
............................................................
----------------------------------------------------------------------
Ran 56 tests in 0.600s

OK
```

每个字符代表一个测试结果:

| 符号 | 含义 |
|---|---|
| `.` | 通过 |
| `F` | 失败(`assertEqual` 等断言不成立) |
| `E` | 错误(代码抛异常崩溃) |
| `s` | 被 skip |
| `x` | 预期失败 |

末尾 `OK` 即全过;`FAILED (failures=N, errors=M)` 则有挂的,需要往上翻看 traceback。

### 详细模式(加 `-v`)

```
test_demo_does_not_pollute_runs_root (...RunArchiveTests.test_demo_does_not_pollute_runs_root)
``_demo`` must write to a tempdir and never touch RUNS_ROOT. ... ok
```

每行三段:**测试方法全路径 + docstring 描述 + 结果**。

---

## 5. 测试失败时怎么定位

挂掉的测试会输出类似这样的内容:

```
FAIL: test_demo_does_not_pollute_runs_root (...)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "tests/test_svfp_loop_logger.py", line 820, in test_demo_does_not_pollute_runs_root
    self.assertEqual(list(self.ra.RUNS_ROOT.iterdir()), [])
AssertionError: Lists differ: [PosixPath('.../demoxx')] != []
```

按这个顺序读:

1. `FAIL: <方法名>` —— 哪个测试挂了
2. `File "..." line N` —— 挂在源代码哪一行
3. `AssertionError: ...` —— 期望值 vs 实际值

然后用"只跑某一个测试"的命令反复改代码 + 重跑,直到变 `ok`。

---

## 6. 速记 cheat sheet

```bash
# 进项目 + 激活 venv (每次开新终端做一次)
cd /Users/mikaelsnow/Documents/ECNU/Paper_Comment_Poster/poster_agent_backend
source .venv312/bin/activate

# 跑全文件 (改完代码全量回归)
python -m unittest tests.test_svfp_loop_logger -v

# 只跑某个类 (针对性测试)
python -m unittest tests.test_svfp_loop_logger.RunArchiveTests -v

# 只跑某个测试方法 (调试某个挂的用例)
python -m unittest tests.test_svfp_loop_logger.RunArchiveTests.test_demo_does_not_pollute_runs_root -v

# 跑 tests/ 下所有测试文件
python -m unittest discover -s tests -v
```

---

## 7. 常见坑

- **`ModuleNotFoundError: No module named 'app'`** —— 99% 是没在项目根目录跑,或者没激活 venv。先确认:
  ```bash
  pwd            # 应该是 .../poster_agent_backend
  which python   # 应该指向 .venv312/bin/python
  ```
- **命令里写成路径形式**(`python -m unittest tests/test_svfp_loop_logger.py`)也能跑,但**类名 / 方法名一旦带上就必须用 `.` 分隔**(`tests.xxx.ClassName.method`),不能用 `/`。
- **`pytest` 没装** —— 本项目坚持只用 `unittest`,因此 `python -m pytest` 会报 `No module named pytest`。如果想用 pytest,先 `pip install pytest`,但目前没必要。

---

## 8. 写新测试时的几个约定

参考 `test_svfp_loop_logger.py` 现有写法:

- 继承 `unittest.TestCase`,方法名以 `test_` 开头才会被发现
- 用 `setUp` / `tearDown` 准备和清理临时资源(如 `tempfile.mkdtemp`)
- 涉及写真实磁盘的模块,通过 monkey-patch 把根路径指到临时目录,避免污染 `outputs/`(见 `RunArchiveTests.setUp`)
- 每个测试方法加一行 docstring,在 `-v` 模式下会作为人类可读标签显示
