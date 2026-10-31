# mytv-android 黑边纯黑补丁

给 [mytv-android](https://github.com/mytv-android/mytv-android)（天光云影）V3.0.0.235 播放器打的**一行补丁**：把播放页四周的"灰边"改成**纯黑**，解决 3:2 / 16:10 等宽屏设备上播放 16:9 视频时上下出现**发灰亮带**的问题。

> 官方包在 3:2 屏上播放 16:9 视频，上下各留约 100px，这圈边不是黑，而是深灰蓝 `#141218`，在暗环境下非常显眼（像漏光）。

---

## 症状

- 播放 16:9 视频，屏幕 3:2 → 上下出现横向条带
- 条带颜色明显比视频内的黑更亮、偏蓝，肉眼看像"屏幕漏光 / 背光不匀"
- 换任何片源都一样，不是片源问题

系统截图逐像素采样结果：

| 区域 | 实测 RGB | 说明 |
|---|---|---|
| 上/下灰边 | `(20, 17, 24)` | ≈ `#141218`，Material 深色主题的 surface 色 |
| 视频内的真实黑位 | `(0, 0, 0)` | 纯黑 |

结论：**不是色彩范围（limited / full range）错配**，也不是 HDR 映射问题——画面本身的黑色是干净的 `0,0,0`，是播放页自己画的那层背景色不黑。

## 根因

播放页用 Compose 实现，根容器整体刷了一层背景色，取值来自 Material3 `ColorScheme` 的 `background` 字段（深色主题 `#141218`），而视频 `SurfaceView` 是按比例居中放置的，于是视频够不到的地方露出这层主题底色。

反编译后对应位置（`smali/e47.smali`，方法内第一个 `background` 绘制点）：

```smali
# 修改前：从主题 ColorScheme 取背景色
iget-wide v1, v4, Lty0;->E:J

# 修改后：硬编码纯黑 Color(0xFF000000)
const-wide v1, 0xff00000000000000L
```

Compose 的 `Color` 是内联值类，`0xAARRGGBB` 存在 long 的高 32 位，所以纯黑写作 `0xff00000000000000L`。

## 补丁

- [`patch/blackbar-pureblack.patch`](patch/blackbar-pureblack.patch) — 人类可读的改动
- [`tools/patch.py`](tools/patch.py) — 自动化脚本（反编译 → 定位 → 替换 → 重打包 → 签名）

**只改了这一处**：其它界面配色、播放内核、功能逻辑、依赖库全部与官方 `V3.0.0.235` 一致。

## 下载

前往 [Releases](../../releases) 下载：

```
mytv-android-tv-3.0.0.235-all-sdk23-blackbar-black.apk
```

- 基于官方 `V3.0.0.235` 的 `all-sdk23-original` 通用包（所有 ABI）
- 大小 34 MB

SHA-256：

```
188949cea33c4ea3e3ea9afbebfa6587c1926988fb560146e831dc729523f3ae
```

## 安装（重要）

补丁版用的是**调试证书**重新签名，签名与原版不同，**不能覆盖安装**：

1. 先卸载设备上原有的「电视直播 / mytv-android」
2. 再安装本 APK
3. 订阅源、频道、设置等本地数据会一起清空，装好后需重新导入

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

## 说明与免责

- 本项目**非官方**，与原项目作者无关；问题反馈请勿提到上游仓库。
- 上游代码为 [MIT License](https://github.com/yaoxieyoulei/mytv-android/blob/feature/ui/LICENSE)（© yaoxieyoulei），本仓库仅分发带一行修改的二进制包及补丁说明，保留原作者版权。
- 仅用于个人设备上的显示效果修正，请勿用于商业分发。
- 如官方后续版本自行修复了该问题（把播放页背景改为纯黑），建议回归官方包。
