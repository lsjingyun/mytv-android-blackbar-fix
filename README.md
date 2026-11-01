# mytv-android 播放显示修正补丁

给 [mytv-android](https://github.com/mytv-android/mytv-android)（天光云影）V3.0.0.235 播放器打的两个小补丁，解决 3:2 / 16:10 等宽屏设备上的显示问题：

| # | 问题 | 修复 |
|---|---|---|
| 1 | 播放 16:9 视频时上下"黑边"发灰，像屏幕漏光 | 播放页背景由主题深灰 `#141218` 改为**纯黑** `#000000` |
| 2 | 选「填充」后画面被非等比拉伸（只有上下拉长） | 「填充」改为**等比放大 + 裁剪两侧**（cover），不再变形 |

> 两个补丁都是改一行级别，界面其它配色、播放内核、功能逻辑、依赖库与官方 `V3.0.0.235` 完全一致。

---

## 一、黑边发灰

### 症状

- 播放 16:9 视频，屏幕 3:2 → 上下出现横向条带
- 条带颜色明显比视频内的黑更亮、偏蓝，肉眼看像"背光不匀 / 漏光"
- 换任何片源都一样，不是片源问题

系统截图逐像素采样结果：

| 区域 | 实测 RGB | 说明 |
|---|---|---|
| 上/下灰边 | `(20, 17, 24)` | ≈ `#141218`，Material 深色主题的 surface 色 |
| 视频内的真实黑位 | `(0, 0, 0)` | 纯黑 |

结论：**不是色彩范围（limited / full range）错配**，也不是 HDR 映射——画面本身的黑色是干净的 `0,0,0`，是播放页自己画的那层背景色不黑。

### 根因与改法

播放页用 Compose 实现，根容器刷了一层背景色，取值来自 Material3 `ColorScheme` 的 `background`（深色主题 `#141218`）。视频是按比例居中放置的，够不到的地方就露出这层主题底色。

```smali
# 修改前：从主题 ColorScheme 取背景色
iget-wide v1, v4, Lty0;->E:J

# 修改后：硬编码纯黑 Color(0xFF000000)
const-wide v1, 0xff00000000000000L
```

Compose 的 `Color` 是内联值类，`0xAARRGGBB` 存在 long 的高 32 位，所以纯黑写作 `0xff00000000000000L`。

## 二、「填充」等比放大

### 症状

- 选「填充」后，画面铺满全屏但被拉伸变形（3:2 屏播 16:9 视频 → 人像明显纵向拉长）
- 期望是"等比放大填满，裁掉超出部分"

### 根因

视频控件是自绘的 `GLSurfaceView`（`VideoEnhancementSurfaceView`），纹理永远铺满控件本身，所以**画面比例完全由 Compose 给的控件尺寸决定**：

| 模式 | 官方实现 | 实际效果 |
|---|---|---|
| 原始（ORIGINAL） | `aspectRatio(视频比例)` | 控件等于画面比例，正确留边 |
| 填充（FILL） | `fillMaxSize()` | 控件撑满屏幕 → **非等比拉伸** ← 问题所在 |
| 裁剪（CROP） | `fillMaxWidth().aspectRatio(视频比例)` | 宽度锁屏宽、高度按比例算出 → **等比缩小**，并没有放大 |

注意「裁剪」用的其实是 `fillMaxWidth`：宽度被锁成屏幕宽，高度按比例算出来比屏幕矮，结果是画面等比缩小、上下留边，看起来和「原始」几乎没区别。这是官方自己的实现问题，不是配置问题。

### 改法

两个分支（填充 `:cond_2c`、裁剪 `:cond_2b`）都改成「锁定屏幕高度 → 按视频比例算宽度 → 宽度超出屏幕的部分由父级裁掉」，并把 `matchHeightConstraintsFirst` 置为 `true` 让宽高比按高度优先计算：

```smali
:cond_2c                                             # 填充分支
const/high16 v1, 0x3f800000                          # 1.0f
const/4 v5, 0x1                                      # matchHeightConstraintsFirst = true
invoke-static {v8, v1}, Lsj7;->b(Ld15;F)Ld15;        # fillMaxHeight(1.0f)
move-result-object v1
iget-object v2, v6, Leu8;->e:Llm7;
invoke-virtual {v2}, Llm7;->r()F                     # 视频真实宽高比
move-result v2
invoke-static {v1, v2, v5}, Lva0;->R(Ld15;FZ)Ld15;   # aspectRatio(ratio, true)
move-result-object v1
```

为什么必须两个条件同时满足：

- `aspectRatio` 的测量类（`Lpr;`）用字段 `p` 保存 `matchHeightConstraintsFirst`。`p = false` 时先按**宽度**算（1080×607，等比缩小），`p = true` 时才先按**高度**算（1280×720，超出屏幕宽度）。
- 高度锁定后，1280 宽超出父约束，带约束检查的候选尺寸全部失败，于是回退到"不检查约束"的结果 → 得到 1280×720 的控件，居中摆放、两侧被窗口裁掉 —— 这才是 cover。

### 混淆对照（容易搞反）

`FillElement` 的方向枚举是 `Lmu1;`，静态初始化里能看到明文枚举名：`a` = `Vertical`、`b` = `Horizontal`、`c` = `Both`。所以：

| 混淆签名 | 真实含义 |
|---|---|
| `Lsj7;->b(Ld15;F)` | `fillMaxHeight(fraction)` ← Vertical |
| `Lsj7;->e(Ld15;F)` | `fillMaxWidth(fraction)` ← Horizontal |
| `Lsj7;->c(Ld15;F)` | `fillMaxSize(fraction)` ← Both |
| `Lva0;->R(Ld15;FZ)` | `aspectRatio(ratio, matchHeightConstraintsFirst)` |

> 只看方法名 `b/c/e` 猜不出谁是谁，必须对照 `Lmu1;` 的 `<clinit>`，否则很容易把 `e`（fillMaxWidth）当成 fillMaxHeight，改完只会得到"等比缩小"。

## 补丁文件

- [`patch/blackbar-pureblack.patch`](patch/blackbar-pureblack.patch) — 黑边改纯黑
- [`patch/fill-uniform-zoom.patch`](patch/fill-uniform-zoom.patch) — 填充改等比放大
- [`tools/patch.py`](tools/patch.py) — 自动化脚本（反编译 → 定位 → 替换 → 重打包 → 签名）
- [`docs/analysis.md`](docs/analysis.md) — 完整排查过程

## 下载

前往 [Releases](../../releases) 下载：

```
mytv-android-tv-3.0.0.235-all-sdk23-blackbar-black.apk
```

- 基于官方 `V3.0.0.235` 的 `all-sdk23-original` 通用包（所有 ABI）
- 大小 34.2 MB

SHA-256：

```
f935c2bebf61ed95f24c35d3b87400854209f7229b8662424e30eb6d4e20ba57
```

## 安装（重要）

补丁版用的是**调试证书**重新签名，签名与原版不同，**不能覆盖安装**：

1. 先卸载设备上原有的「电视直播 / mytv-android」
2. 再安装本 APK
3. 订阅源、频道、设置等本地数据会一起清空，装好后需重新导入

同系列补丁版之间签名一致，可以互相覆盖升级：

```bash
adb install -r mytv-android-tv-3.0.0.235-all-sdk23-blackbar-black.apk
```

## 自己复现 / 打补丁

需要 JDK 17、[apktool](https://github.com/iBotPeaches/Apktool) 2.11.x、[uber-apk-signer](https://github.com/patrickfav/uber-apk-signer)。

```bash
# 1. 下载官方包
curl -LO https://github.com/mytv-android/mytv-android/releases/download/V3.0.0.235/mytv-android-tv-3.0.0.235-all-sdk23-original.apk

# 2. 打补丁并重打包（脚本内已做签名与对齐）
python3 tools/patch.py orig.apk -o patched.apk
```

脚本里的定位依赖 235 这一版的具体指令序列，换版本若报 `expected exactly one ...` 需要重新定位（方法见 `docs/analysis.md`）。

## 说明与免责

- 本项目**非官方**，与原项目作者无关；问题反馈请勿提到上游仓库。
- 上游代码为 [MIT License](https://github.com/yaoxieyoulei/mytv-android/blob/feature/ui/LICENSE)（© yaoxieyoulei），本仓库仅分发带修改的二进制包及补丁说明，保留原作者版权。
- 仅用于个人设备上的显示效果修正，请勿用于商业分发。
- 如官方后续版本自行修复了这些问题，建议回归官方包。
