# 排查过程

## 1. 先排除片源与色彩范围问题

很多人第一反应是"limited / full range 错配"或"HDR 映射把黑电平抬高了"。用系统截图（不是相机拍屏）逐像素采样即可排除：

```
区域            实测 RGB
上边条带        (20, 17, 24)
下边条带        (20, 17, 24)
视频内的暗部    (0, 0, 0)
```

如果真是色彩范围错配，视频内的黑色也会被抬到 16 左右，而不是精确的 0。画面黑得干净、只有边条发灰，说明**边条是另一层东西画的**。

## 2. 灰边到底是哪个颜色

`(20, 17, 24)` = `#141218` —— 这正是 Material3 深色配色的 `background` / `surface` 基色，带一点点蓝调。全屏搜索 `141218` 在资源与 dex 中都没有字面量，说明它是 Compose 侧算出来的（`ColorScheme` 构造时生成，而非 XML 资源），所以从 `res/values/colors.xml` 里找不到。

## 3. 定位到绘制点

R8 混淆后方法名全没了，但**外部库的方法名还在**，可以拿它当锚点：

- Compose 的 `Modifier.background(color, shape)` 被混淆成 `Lfn3;->q(Ld15;JLme7;)Ld15;`
- `Modifier.aspectRatio(float)` 被混淆成 `Lsj7;->c(Ld15;F)Ld15;`

在反编译树中搜索 `Lfn3;->q(Ld15;JLme7;)Ld15;` 的调用点，找到播放页里那一处参数直接来自 `ty0`（`ColorScheme`）字段 `E` 的调用：

```smali
iget-object v4, v4, Ldo4;->a:Lty0;   # 取到 ColorScheme
iget-wide v1, v4, Lty0;->E:J         # 读 background 颜色
sget-object v4, Lgn3;->J0:Lt83;
invoke-static {v0, v1, v2, v4}, Lfn3;->q(Ld15;JLme7;)Ld15;
```

同一方法内紧接着就是视频视图的 `AndroidView` factory（`Lmb5;`），确认这就是播放页根容器那层背景。

## 4. 为什么写成 `0xff00000000000000L`

Compose 的 `Color` 是 Kotlin 内联值类，内部就是 `ULong`，把 `0xAARRGGBB` 放在**高 32 位**：

```java
Color(0xFF000000)  // -> 0xFF000000_00000000
```

所以在 smali 里写 `const-wide v1, 0xff00000000000000L`。

## 5. 修改后验证

- `apktool b` 重新打包成功
- `.so` 仍以**未压缩**方式存储（与官方包一致，`extractNativeLibs=false` 要求）
- uber-apk-signer 签名，v1 + v2 + v3 全部校验通过
- 回编译产物中确认替换指令已存在，且没有误伤其它 `background` 调用点

> 说明：官方 `main` 分支目前只保留 README（源码已转入内部私有分支），所以只能走"反编译官方 APK"的路线。上游开源代码 `feature/ui` 分支的 `VideoPlayerScreen.kt` 里，视频确实是按比例居中放置的 `SurfaceView`，四周露出的是 Compose 背景，与上述结论一致。

---

# 第二个问题：「填充」非等比拉伸

## 现象

选「填充」后画面铺满全屏，但人像明显纵向拉长；期望是等比放大、裁掉超出部分。

## 关键认知：画面比例完全由控件尺寸决定

这个 APK 的视频控件不是 `SurfaceView` 直出，而是自绘的 `VideoEnhancementSurfaceView`（继承 `GLSurfaceView`，带 Vulkan/超分增强管线）。它的 GL 渲染是固定全屏四边形，纹理永远铺满控件本身 —— 所以**视频比例 100% 由 Compose 给的控件尺寸决定**，改解码器、改 `setVideoScalingMode` 都没有用。

## 定位

`VideoPlayerDisplayMode` 枚举（`Ltt8;`）有 6 个值：ORIGINAL/FILL/CROP/FOUR_THREE/SIXTEEN_NINE/WIDE。播放页里对 `ordinal()` 做了一串 `if-eq` 分派，每个分支拼出不同的 Compose 修饰符链：

| 模式 | 修饰符链 | 效果 |
|---|---|---|
| 原始 | `aspectRatio(视频比例)` | 控件 = 画面比例，正确留边 |
| 填充 | `fillMaxSize()` | 控件撑满屏幕 → **非等比拉伸** |
| 裁剪 | `fillMaxHeight().aspectRatio(视频比例)` | 等比放大 + 裁两侧 ✅ |

混淆后的函数对照：

| 签名 | 含义 |
|---|---|
| `Lsj7;->b(Ld15;F)` | `fillMaxWidth(fraction)` |
| `Lsj7;->e(Ld15;F)` | `fillMaxHeight(fraction)` |
| `Lsj7;->c(Ld15;F)` | `fillMaxSize(fraction)` |
| `Lva0;->R(Ld15;FZ)` | `aspectRatio(ratio, matchHeightConstraintsFirst)` |

判定方法：`Lsj7;` 里的 `b/c/e` 都是构造 `FillElement`（`Lqm2;`），差别只在传入的 `Direction`（`Lmu1;` 的 a/b/c）。对照 `<clinit>` 里缓存的三个实例即可确定谁是谁。

## 为什么 `fillMaxHeight + aspectRatio` 会是"超出屏幕"的等比放大

Compose 的 `AspectRatioNode` 会依次尝试若干候选尺寸：先在**满足父约束**的前提下试 `maxWidth` / `maxHeight` / `minWidth` / `minHeight`，都不行时再用**不做范围检查**的版本（反编译里那两轮 `z=true` / `z=false` 的 `J0/I0/L0/K0`）。

`fillMaxHeight()` 先把高度锁定为屏幕高，此时 16:9 视频需要 1281px 宽，超过屏幕的 1080px，所有带范围检查的候选都失败，于是退回 1281×720 —— 比例正确、比屏幕更宽，居中绘制，两侧自然被窗口裁掉。这就是"等比放大裁剪"。

## 改法

把「填充」分支换成「裁剪」的算法，结果寄存器沿用原来的 `v6`：

```smali
:cond_1b
const/4 v4, 0x0
invoke-static {v2, v6}, Lsj7;->e(Ld15;F)Ld15;      # fillMaxHeight(1.0f)
move-result-object v3
invoke-virtual/range {v17 .. v17}, Llm7;->r()F     # state.aspectRatio
move-result v6
invoke-static {v3, v6, v4}, Lva0;->R(Ld15;FZ)Ld15;  # aspectRatio(ratio)
move-result-object v6
```

`v17` 就是 `VideoPlayerState`，`Llm7;->r()` 返回当前视频的宽高比（同一个方法里的「原始」分支也是这么取的）。
