# 800 Gbps 相干光链路 RS Python 模型

按 `RS_CODEC_REQUIREMENTS.md` 的数学及字节接口约定实现纯 RS(255,239)。本次交付范围是用户要求的 **Python 模型**，不是文档中的完整 SystemVerilog 工程。核心与测试仅依赖 Python 标准库，在 Python 3.12.14 验证；Ruff 仅为可选开发工具。

这是相干接收 DSP 硬判决之后、外部解交织与码字定界之后的 FEC 模型。发送端提供对应系统编码。没有实现光前端、复数采样、DP-64QAM DSP、交织、LDPC、OTN/800ZR 协议或 BER 认证。

## 运行

```bash
cd /workspace/gpt_test
source /workspace/.venvs/gpt_test/bin/activate
make test                 # 全部数学、流控回归和四种场景周期调度测试
make lint                 # 需要可选的 Ruff
make vectors              # 常量、完整码字、SHA-256 和伴随式 JSON
make demo                 # 固定种子、8 字节错误纠错示例
python -m rs_codec budget
python -m rs_codec demo --errors 9 --seed 20261030
```

不使用虚拟环境时直接运行 `python3 -m unittest discover -s tests -v` 和 `python3 -m tools.throughput` 即可，无需 pip 安装。`make` 默认使用 `python3`，可以通过 `make PYTHON=/path/to/python test` 指定解释器。

```python
from rs_codec import encode, decode, inject_errors, Status

information = bytes(range(239))
codeword = encode(information)              # 239 信息字节 + 16 校验字节
received = inject_errors(codeword, [0, 238, 239, 254], [1, 128, 255, 7])
result = decode(received)
assert result.data == information
assert result.status == Status.CORRECTED
assert result.error_count == 4              # 包括校验区错误
```

命令行也支持 `python -m rs_codec encode <239字节十六进制>` 与 `python -m rs_codec decode <255字节十六进制>`。译码命令输出 JSON，包含数据、完整结果码字、状态、错误数、位置、错误值和失败原因。

## 数学约定

- GF(256)：本原多项式 0x11D、本原元 0x02、字节 bit i 表示 x^i。
- 生成多项式 `∏(x+α^j), j=0..15`，首根 0；系数高次在前。
- 编码提供独立长除法和逐字节 LFSR 两种实现。
- 译码使用经典 Berlekamp–Massey、Chien 搜索、Forney 及纠错后完整伴随式检查。
- Λ、Ω 内部按低次在前保存；接收字节 r[p] 对应 x^(254-p)。Forney 使用 `X*Ω(X^-1)/Λ'(X^-1)`，包含 b=0 必需的 X 因子。
- 一次定位多项式采用等价闭式定位 `p=254-logα(Λ[1])` 加速全单错测试，其余次数搜索所有 255 个合法位置。
- `gf_inv(0)=0`；除零仍抛错，译码中零分母判失败。
- 失败时 `data=received[:239]`、`codeword=received`、`error_count=0`，不会输出部分纠正的数据。
- 超过 8 错不能保证检测。测试包括 192 个代表性输入，以及特意构造的合法码字漏检和向其他码字误纠，绝不把失败标志当成 CRC。
- `solve_error_values()` 使用已知位置的 GF 高斯消元独立检查 Forney 错误值。第三方库不参与核心模型。

## 32 字节接口与 8 路全双工模型

`rs_codec.stream.StreamCore` 模拟单路，`Duplex(lanes=8)` 提供独立的 8 编码路和 8 译码路，另支持 `lanes=1`。

```python
from rs_codec.stream import StreamCore, pack_beats
core = StreamCore('encode')
beats = pack_beats(bytes(239), cw_id=0xFFFFFFFF)
# 每拍调用 core.step(source=beat_or_None, m_ready=True, rst_n=True,
#                    stats_clear=False)。返回边沿前信号，再执行本拍握手。
# 只有返回 s_ready 时输入才被接受；否则下一拍必须继续提供同一个 Beat。
```

`source=None` 表示 valid=0。输出 `Signals` 包含 s_ready、m_valid 和可选 Beat；m_valid 不依赖 m_ready。Beat 不可变，包含 32 字节 data、keep、sop、eop、32 bit ID、status 和 error_count。字节 0 位于整数 data 的最低 8 bit，`data_int` 提供对应小端总线值。尾拍 keep 分别为 0x7FFF/0x7FFFFFFF，输出无效 lane 清零，输入无效 lane 不参与运算。

接口检查非法长度/keep、SOP/EOP、ID 改变以及上游反压时载荷或 valid 改变，抛出 ProtocolError。每路默认 8 个整码字槽，满时反压，不覆盖旧数据；输出按路保序。同步低有效复位清除在途事务和计数；stats_clear 只清计数且优先于同拍计数事件。计数器 64 bit 饱和，输入/输出在 EOP 握手时计数。

这是整码字存储转发的**行为调度模型**，编码首输出延迟假设为 16 拍、译码为 48 拍；输入有气泡或输出反压时可能延长。没有模拟 RiBM/KES、Chien 硬件流水级、RAM 端口或门延迟。纯函数结果最多缓存 256 个重复负载以加速仿真，不缓存 ID 或握手状态。

## 800 Gbps 预算与性能解释

DP-64QAM 每符号周期两个偏振合计 12 bit。800 Gbps 净速率经 RS 后为 853.556485 Gbps，最低 71.129707 GBd；额外乘数 1.02 时需 72.552301 GBd。75 GBd 提供 900 Gbps 线路容量。这些是预算，不是光接口符合性证明。

每路 239 字节/9 拍、8 路、假设 500 MHz：每个方向信息字段容量为 **849.777778 Gbps**，编码有效字节容量为 **906.666667 Gbps**。不能使用总线峰值 1024 Gbps 替代持续有效吞吐。

`make throughput-test` 对无错、每码字 8 错、明确失败、混合流分别运行，8 路发送和接收同时独立工作。每场景稳态窗口 10,008 拍（排除填充及排空），按真实握手字节计数，每方向输出 8,896 个码字。逐拍 scoreboard 检查内容、状态、ID、尾拍与 II=9。首输入 SOP 接受到输出 SOP/EOP valid 的周期差分别为 TX 16/23、RX 48/55；不含反压。

**上述速率仅证明调度模型按指定延迟/带宽假设可以容纳目标流量，不能证明 Python 实时处理达到 800 Gbps，也不能证明 RTL 达到 II=9 或 500 MHz。** 失败场景速率表示传输原始信息字段的容量，不表示成功恢复的有效业务 goodput。没有 BER≤10^-15 的结论。

## 验证与文件

- `rs_codec/gf256.py`：有限域和独立线性方程求解。
- `rs_codec/rs255239.py`：编码、译码、伴随式和错误注入。
- `rs_codec/stream.py`：周期握手、缓冲、统计和全双工模型。
- `rs_codec/budget.py`：线路及数字处理速率预算。
- `tests/`：GF 全 65,536 对输入、全部逆元、5 固定向量及 SHA-256、1,000 随机编码、0～8 错每种 256 组、全部 65,025 单错、边界/同批 8 根/仅校验区、超半径、协议/复位/反压/统计回归。失败案例带固定种子及可重放的码字信息。
- `tools/`：生成向量、全双工周期调度检查。
- `reports/verification.md`：验收边界；`test.log`：实际回归日志；`throughput.json`：实际模型调度统计；`vectors.json`：生成的完整向量。

本仓库没有 RTL、综合目标和工艺库，因此不提供会伪装通过的 rtl-test/synth。A3/A4 只有 Python 模型证据，A5/A6 未验证。开发环境配置未被发布，Git 提交和推送由用户决定。
