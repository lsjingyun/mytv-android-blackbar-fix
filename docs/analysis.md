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

# 第二个问题：「填充」拉伸 / 「裁剪」不放大

## 现象

选「填充」，画面铺满屏幕但人像明显纵向拉长；期望是等比放大、裁掉超出部分。
另外「裁剪」模式看起来和「原始」几乎一样，同样不符合预期。

## 关键认知：画面比例完全由控件尺寸决定

视频控件不是普通 `SurfaceView` 直出，而是自绘的 `VideoEnhancementSurfaceView`
（继承 `GLSurfaceView`，带超分 / Vulkan 渲染管线）。GL 的渲染是固定全屏四边形，
纹理永远铺满控件本身 —— 所以**视频比例 100% 由 Compose 分配给控件的尺寸决定**，
改解码器、改 `MediaCodec.setVideoScalingMode` 都不会有任何效果。

## 定位到的真实代码

播放页是 `e47.n(Ld15;Leu8;...)`（`Leu8` 是 VideoPlayerState，`PlayerActivity` 的
内容 composable `fw5` 最终会走到这里）。方法内对显示模式的 ordinal 做了一串
`if-eq` 分派：

| ordinal | 模式 | 官方实现 |
|---|---|---|
| 0 | 原始 | `aspectRatio(state.aspectRatio)` |
| 1 | 填充 | `fillMaxSize(1.0f)`（`:cond_2c`） |
| 2 | 裁剪 | `fillMaxWidth(1.0f).aspectRatio(ratio)`（`:cond_2b`） |
| 3 | 4:3 | `aspectRatio(1.3333f)` |
| 4 | 16:9 | `aspectRatio(1.7778f)` |
| 5 | 2.35:1 | `aspectRatio(2.35f)` |

## 三轮踩坑记录（都是真实踩过的）

### 坑 1：改在了没人调用的播放页

第一次改的是 `v57.smali` 里的同名分支。它确实是播放页逻辑，但那个页面没有被
`PlayerActivity` 走到，改完完全没有效果。判断方法：看 `PlayerActivity.onCreate`
里 `setContent` 出去的 composable（`fw5`）最终调用谁；再看 `mb5`（唯一创建
`VideoEnhancementSurfaceView` 的 factory）被哪个方法引用 —— 两处都指向 `e47`。

### 坑 2：`Lsj7;` 的三个方法名搞反了

以为 `b/c/e` = fillMaxWidth / fillMaxSize / fillMaxHeight，于是把填充改成了
`fillMaxWidth().aspectRatio(ratio)`。结果宽度被锁成屏幕宽 1080，高度按 16:9
算出 607 —— 画面等比缩小、上下留黑边，看起来和「原始」一模一样，所以当时的
反馈是"填充和裁剪都没变化"。

正确做法是去 `Lmu1;`（FillElement 的 Direction 枚举）的 `<clinit>` 里读明文
枚举名：

```
Lmu1;->a = "Vertical"     -> fillMaxHeight
Lmu1;->b = "Horizontal"   -> fillMaxWidth
Lmu1;->c = "Both"         -> fillMaxSize
```

所以 `Lsj7;->b` 才是 `fillMaxHeight`，`Lsj7;->e` 是 `fillMaxWidth`。
顺带发现官方「裁剪」用的就是 `e`，难怪它也只会缩小 —— 那是官方实现的 bug。

### 坑 3：`aspectRatio` 的默认是"宽度优先"

`Lpr;`（AspectRatioNode）用字段 `p` 保存 `matchHeightConstraintsFirst`：

- `p = false`：先走 `J0`（按 maxWidth 算高度）→ 1080×607，等比缩小
- `p = true`：先走 `I0`（按 maxHeight 算宽度）→ 1280×720，超出屏幕宽度

`c()` 里会依次尝试 4 个带约束检查的候选（z=true），全失败后再试 4 个不检查
约束的（z=false）；如果 8 个都返回 Invalid，才退化成"直接用父约束测量"
（那就是纯拉伸）。所以在 `fillMaxHeight()` 锁死高度后，必须把
`matchHeightConstraintsFirst` 置为 `true`，才能拿到 1280×720 这种超约束尺寸。

## 最终改法

`:cond_2b`（裁剪）与 `:cond_2c`（填充）两个分支都改成：

```smali
const/high16 v1, 0x3f800000                     # 1.0f
const/4 v5, 0x1                                 # matchHeightConstraintsFirst = true
invoke-static {v8, v1}, Lsj7;->b(Ld15;F)Ld15;   # fillMaxHeight(1.0f)
move-result-object v1
iget-object v2, v6, Leu8;->e:Llm7;
invoke-virtual {v2}, Llm7;->r()F                 # 视频比例
move-result v2
invoke-static {v1, v2, v5}, Lva0;->R(Ld15;FZ)Ld15;  # aspectRatio(ratio, true)
move-result-object v1
goto :goto_1d
```

`v6` 在该分支上下文中就是 `Leu8`（VideoPlayerState），`Llm7;->r()` 取的是
`state.aspectRatio`，和「原始」分支用的是同一个取法。
