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
