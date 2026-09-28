# Google Go 风格指南

> 译自 Google 的 [Go Style Guide](https://google.github.io/styleguide/go/) 中的总览与[指南](https://google.github.io/styleguide/go/guide)两页，
> 原文以 [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/) 许可发布。
> 本文件是其简体中文译本，供编码与审查时查阅：标题的层级与顺序照原文（为挂在译本标题之下，
> 指南页的各级标题整体下移了一级），代码示例、标识符与注释一律保留英文原文。
>
> 原文分四页，译本对应三个文件：本文件（总览 + 指南）、[`go-decisions.md`](go-decisions.md)（风格决策）、
> [`go-best-practices.md`](go-best-practices.md)（最佳实践）。


<a id="about"></a>

## 关于

《Go 风格指南》及配套文档把当前编写可读且地道 Go 代码的最佳做法整理成文。遵守风格指南并不要求绝对化，这些文档也永远不会穷尽所有情况。我们的目的是尽量减少编写可读 Go 代码时的猜测成分，让初学这门语言的人能够避免常见错误。风格指南还用于统一 Google 内部任何人评审 Go 代码时给出的风格指导。

文档                | 链接                                                  | 主要读者       | [Normative] | [Canonical]
------------------- | ----------------------------------------------------- | -------------- | ----------- | -----------
**风格指南**        | https://google.github.io/styleguide/go/guide          | 所有人         | 是          | 是
**风格决策**        | https://google.github.io/styleguide/go/decisions      | 可读性导师     | 是          | 否
**最佳实践**        | https://google.github.io/styleguide/go/best-practices | 任何感兴趣的人 | 否          | 否

[Normative]: #normative
[Canonical]: #canonical

<a id="docs"></a>

### 文档

1.  **[风格指南](go-style.md)** 概述了 Google Go 风格的基础。这份文档是权威性的，风格决策与最佳实践中的建议都以它为依据。

1.  **[风格决策](go-decisions.md)** 篇幅更长，它总结了针对具体风格要点所做的决策，并在合适的地方讨论决策背后的理由。

    这些决策偶尔会因新数据、新语言特性、新库或新兴模式而变化，但 Google 的普通 Go 程序员并不需要随时跟进这份文档。

1.  **[最佳实践](go-best-practices.md)** 记录了一些随时间演进而来的模式，它们能解决常见问题、读起来顺畅，并且经得起代码维护的需要。

    这些最佳实践不具权威性，但鼓励 Google 的 Go 程序员在可能的地方采用它们，以保持代码库统一、一致。

这些文档旨在：

*   就权衡不同风格的一套原则达成共识
*   把 Go 风格中已有定论的事项整理成文
*   记录 Go 惯用法并给出权威示例
*   记录各种风格决策的利弊
*   帮助减少 Go 可读性评审中的意外
*   帮助可读性导师使用一致的术语与指导

这些文档**不**旨在：

*   穷举可读性评审中可能给出的所有意见
*   列出每个人在任何时候都应记住并遵守的全部规则
*   取代在语言特性与风格运用上的良好判断力
*   为消除风格差异而进行的大规模改动提供理由

Go 程序员之间、团队代码库之间总会存在差异。然而，让我们的代码库尽可能一致，最符合 Google 与 Alphabet 的利益。（关于一致性，详见 [指南](go-style.md#consistency)。）为此，你可以按自己的判断自由地做风格改进，但不必对你发现的每一处违反风格指南之处吹毛求疵。尤其是，这些文档可能随时间变化，而这并不能成为在既有代码库中制造额外无谓改动的理由；只要用最新的最佳实践编写新代码，并随时间推移逐步处理周边问题，就足够了。

必须认识到，风格问题本质上是因人而异的，而且总存在固有的取舍。这些文档中的许多指导是主观的，但正如 `gofmt` 一样，它们带来的统一性具有重要价值。因此，风格建议不会未经充分讨论就更改；即使并不认同，也鼓励 Google 的 Go 程序员遵循风格指南。

<a id="definitions"></a>

## 定义

以下词语通用于各风格文档，其含义定义如下：

*   **权威**：确立具有规定性且持久有效的规则 <a id="canonical"></a>

    在这些文档中，「权威」用来描述被视为标准的东西：所有代码（无论新旧）都应遵循它，且它不应随时间发生实质变化。权威文档中的原则应被作者与评审者共同理解，因此凡纳入权威文档的内容都必须达到很高的门槛。正因如此，权威文档通常比非权威文档更短，规定的风格要素也更少。

    https://google.github.io/styleguide/go#canonical

*   **规范性**：意在建立一致性 <a id="normative"></a>

    在这些文档中，「规范性」用来描述经一致同意的风格要素，供 Go 代码评审者使用，以保证建议、术语与理由都保持一致。这些要素可能随时间变化，而这些文档会反映此类变化，让评审者保持一致并跟上最新情况。Go 代码的作者不必熟悉规范性文档，但评审者在可读性评审中会频繁地把这些文档当作参考。

    https://google.github.io/styleguide/go#normative

*   **地道**：常见且熟悉 <a id="idiomatic"></a>

    在这些文档中，「地道」用来指在 Go 代码中普遍存在、已成为易于识别的熟悉模式的做法。一般而言，如果两种做法在给定语境下目的相同，应当优先选择地道的模式而非不地道的做法，因为地道的模式对读者而言最为熟悉。

    https://google.github.io/styleguide/go#idiomatic

<a id="references"></a>

## 其他参考

本指南假定读者熟悉 [Effective Go]，它为整个 Go 社区的 Go 代码提供了共同的基准。

以下是更多资源，供希望自学 Go 风格的人、以及在评审中想提供更多可链接背景的评审者使用。Go 可读性流程的参与者不必熟悉这些资源，但它们可能会作为背景出现在可读性评审中。

[Effective Go]: https://go.dev/doc/effective_go

**外部参考**

*   [Go 语言规范](https://go.dev/ref/spec)
*   [Go 常见问题](https://go.dev/doc/faq)
*   [Go 内存模型](https://go.dev/ref/mem)
*   [Go 数据结构](https://research.swtch.com/godata)
*   [Go 接口](https://research.swtch.com/interfaces)
*   [Go 谚语](https://go-proverbs.github.io/)

*   <a id="gotip"></a> Go Tip 系列 - 敬请期待。

*   <a id="unit-testing-practices"></a> 单元测试实践 - 敬请期待。

**Testing on the Toilet 相关文章**

*   [TotT：标识符命名][tott-431]
*   [TotT：测试状态与测试交互][tott-281]
*   [TotT：有效测试][tott-324]
*   [TotT：风险驱动测试][tott-329]
*   [TotT：变更探测器式测试有害][tott-350]

[tott-431]: https://testing.googleblog.com/2017/10/code-health-identifiernamingpostforworl.html
[tott-281]: https://testing.googleblog.com/2013/03/testing-on-toilet-testing-state-vs.html
[tott-324]: https://testing.googleblog.com/2014/05/testing-on-toilet-effective-testing.html
[tott-329]: https://testing.googleblog.com/2014/05/testing-on-toilet-risk-driven-testing.html
[tott-350]: https://testing.googleblog.com/2015/01/testing-on-toilet-change-detector-tests.html

**其他外部文章**

*   [Go 与教条](https://research.swtch.com/dogma)
*   [少，却指数级地多](https://commandcenter.blogspot.com/2012/06/less-is-exponentially-more.html)
*   [Esmerelda 的想象](https://commandcenter.blogspot.com/2011/12/esmereldas-imagination.html)
*   [用于解析的正则表达式](https://commandcenter.blogspot.com/2011/08/regular-expressions-in-lexing-and.html)
*   [Gofmt 的风格没人喜欢，但 Gofmt 是每个人的最爱](https://www.youtube.com/watch?v=PAAkCSZUG1c&t=8m43s) （YouTube）

## Go 风格指南

**注意：**本文是概述 Google [Go 风格](go-style.md) 的一系列文档之一。本文是**[规范性](go-style.md#normative)的，也是[权威](go-style.md#canonical)的**。更多信息见[总览](go-style.md#about)。

<a id="principles"></a>

### 风格原则

有几条总体原则概括了编写可读 Go 代码的思考方式。以下是可读代码应具备的属性，按重要性排序：

1.  **[Clarity]**：代码的目的与理由对读者而言是清楚的。
1.  **[Simplicity]**：代码以尽可能最简单的方式达成目标。
1.  **[Concision]**：代码具有高信噪比。
1.  **[Maintainability]**：代码的写法使其易于维护。
1.  **[Consistency]**：代码与更广范围的 Google 代码库保持一致。

[Clarity]: #clarity
[Simplicity]: #simplicity
[Concision]: #concision
[Maintainability]: #maintainability
[Consistency]: #consistency

<a id="clarity"></a>

#### 清晰

可读性的核心目标是产出对读者而言清晰的代码。

清晰主要通过有效的命名、有帮助的注释以及高效的代码组织来实现。

应当从读者的视角、而非代码作者的视角来看待清晰。代码易于阅读比易于编写更重要。代码的清晰有两个不同侧面：

*   [代码实际上在做什么？](#clarity-purpose)
*   [代码为什么这样做？](#clarity-rationale)

<a id="clarity-purpose"></a>

##### 代码实际上在做什么？

Go 的设计使得看清代码在做什么应当相对直截了当。在存在不确定性、或读者需要具备先前知识才能理解代码的情况下，值得花时间让代码的目的对未来的读者更清楚。例如，以下做法可能有帮助：

*   使用更具描述性的变量名
*   补充额外的注释
*   用空白行与注释把代码分隔开
*   把代码重构为独立的函数/方法，使其更模块化

这里没有放之四海而皆准的做法，但在开发 Go 代码时，优先考虑清晰是很重要的。

<a id="clarity-rationale"></a>

##### 代码为什么这样做？

代码的理由往往能由变量、函数、方法或包的名称充分传达。在传达不了的地方，补充注释就很重要。当代码包含读者可能不熟悉的细微之处时，「为什么」尤其重要，例如：

*   语言层面的细微之处，例如某个闭包会捕获一个循环变量，但该闭包距离它有很多行
*   业务逻辑层面的细微之处，例如某项访问控制检查需要区分真实用户与冒充用户的人

某个 API 可能需要小心才能正确使用。例如，一段代码可能出于性能原因而错综复杂、不易跟进，或者一长串复杂的数学运算可能以出人意料的方式使用类型转换。在这些以及更多情形下，重要的是让随附的注释与文档解释这些方面，使未来的维护者不至于犯错，也让读者无需逆向工程就能理解代码。

同样重要的是要意识到，某些意在提供清晰的尝试（例如添加额外注释）反而可能因为增加杂乱、重复代码已经表达的内容、与代码矛盾，或为让注释保持最新而增加维护负担，从而掩盖代码的目的。让代码自己说话（例如让符号名称本身就能自我说明），而不是添加冗余注释。注释通常最好是解释为什么这样做，而不是代码在做什么。

Google 的代码库总体上统一且一致。突出的代码（例如使用了不熟悉的模式）往往是出于充分的理由，通常是为了性能。维持这一特性很重要，它能让读者清楚地知道在阅读一段新代码时该把注意力放在哪里。

标准库中有许多体现这一原则的实例，其中包括：

*   [`package sort`](https://cs.opensource.google/go/go/+/refs/tags/go1.19.2:src/sort/sort.go) 中的维护者注释。
*   同一包中优秀的[可运行示例](https://cs.opensource.google/go/go/+/refs/tags/go1.19.2:src/sort/example_search_test.go)，它们对使用者有益（它们[会出现在 godoc 中](https://pkg.go.dev/sort#pkg-examples)），对维护者也有益（它们[作为测试的一部分运行](go-decisions.md#examples)）。
*   [`strings.Cut`](https://pkg.go.dev/strings#Cut) 只有四行代码，却改善了[调用点的清晰与正确](https://github.com/golang/go/issues/46336)。

<a id="simplicity"></a>

#### 简单

你的 Go 代码应当对使用它、阅读它和维护它的人而言是简单的。

Go 代码应当以达成目标的最简单方式编写，在行为与性能两方面都是如此。在 Google 的 Go 代码库中，简单的代码：

*   从上到下都易于阅读
*   不假定你已经知道它在做什么
*   不假定你能记住前面所有的代码
*   没有不必要的抽象层级
*   没有会把注意力引向平凡之处的名称
*   让值与决策的传递对读者而言清晰
*   其注释解释代码为什么这样做、而非在做什么，以避免将来偏离
*   其文档能够独立成立
*   有有用的错误与有用的测试失败信息
*   常常可能与「聪明」的代码互斥

在代码的简单与 API 用法的简单之间可能出现取舍。例如，让代码更复杂一点，从而使 API 的最终使用者更容易正确调用它，可能是值得的。相反，把一点额外工作留给 API 的最终使用者，从而让代码保持简单易懂，也可能值得。

当代码需要复杂性时，这种复杂性应当是有意添加的。在需要额外性能，或某个库/服务有多个差异很大的使用者时，这通常是必要的。复杂性可能是合理的，但它应当配有随附文档，使调用方与未来的维护者能够理解并驾驭这种复杂性。还应当辅以展示其正确用法的测试与示例，尤其是在代码既有「简单」用法又有「复杂」用法时。

这一原则并不意味着复杂代码不能在 Go 中编写、或不应在 Go 中编写，也不意味着不允许 Go 代码复杂。我们力求代码库避免不必要的复杂性，这样当复杂性真的出现时，它就表明相关代码需要谨慎地理解与维护。理想情况下，应当有随附注释解释其理由，并指出需要谨慎之处。这在为性能优化代码时经常出现；这样做通常需要更复杂的方法，比如预分配一个缓冲区并在整个 goroutine 生命周期内复用它。当维护者看到这种情况时，应当能意识到相关代码是性能关键的，这会影响将来做改动时的谨慎程度。反过来，如果这种复杂性是被不必要地引入的，它就会成为将来需要阅读或修改代码之人的负担。

如果代码在其目的本应简单时却变得非常复杂，这往往是一个信号，提示重新审视实现，看看是否有更简单的方式达成同样的目的。

<a id="least-mechanism"></a>

##### 最小机制

当有多种方式表达同一想法时，优先选用使用最标准工具的那一种。精巧的机制常常存在，但不应当无理由地使用。按需给代码增加复杂性很容易，而在发现已有的复杂性并不必要之后再将其移除则难得多。

1.  当核心语言构造（例如通道、切片、map、循环或结构体）足以满足你的用例时，争取使用它。
2.  如果没有，就在标准库里找工具（例如 HTTP 客户端或模板引擎）。
3.  最后，在引入新依赖或自己造轮子之前，先考虑 Google 代码库中是否有足够的核心库。

举个例子，考虑这样一段生产代码：其中有一个绑定到变量的标志，它带有一个必须在测试中被覆盖的默认值。除非你打算测试程序本身的命令行接口（比如用 `os/exec`），否则直接覆盖所绑定的值比使用 `flag.Set` 更简单，因而更可取。

类似地，如果一段代码需要做集合成员检查，一个布尔值 map（例如 `map[string]bool`）通常就够用了。只有当需要更复杂的操作，且用 map 无法实现或过于复杂时，才应当使用提供类集合类型与功能的库。

<a id="concision"></a>

#### 简洁

简洁的 Go 代码具有高信噪比。相关细节易于辨别，命名与结构则引导读者看懂这些细节。

有许多东西会妨碍在任何时刻凸显最突出的细节：

*   重复的代码
*   多余的语法
*   [晦涩的名称](#naming)
*   不必要的抽象
*   空白

重复的代码尤其会掩盖各段几乎相同的代码之间的差异，并要求读者用眼睛去比较相似的行来找出改动。[Table-driven testing] 就是一个很好的机制示例，它能简洁地把公共代码从每次重复的重要细节中抽离出来；不过，选择把哪些部分放进表里，会影响表有多容易理解。

在考虑多种组织代码的方式时，值得想想哪种方式能让重要细节最为显眼。

理解并使用常见的代码构造与惯用法，对保持高信噪比也很重要。例如，下面这段代码块在 [error handling] 中非常常见，读者能很快理解这段代码块的目的。

```go
// Good:
if err := doSomething(); err != nil {
    // ...
}
```

如果代码看起来与这非常相似但有细微差别，读者可能注意不到变化。在这种情况下，值得有意去 ["boosting"] 错误检查的信号，即添加一条注释来提醒注意。

```go
// Good:
if err := doSomething(); err == nil { // if NO error
    // ...
}
```

[Table-driven testing]: https://go.dev/wiki/TableDrivenTests
[error handling]: https://go.dev/blog/errors-are-values
["boosting"]: go-best-practices.md#signal-boost

<a id="maintainability"></a>

#### 可维护性

代码被编辑的次数远多于被编写的次数。可读的代码不仅对想理解其工作方式的读者有意义，对需要修改它的程序员也有意义。清晰是关键。

可维护的代码：

*   让未来的程序员容易正确地修改
*   其 API 的结构使其能够优雅地演进
*   清楚地表明自己所做的假设，并选择映射问题结构而非代码结构的抽象
*   避免不必要的耦合，不包含未被使用的功能
*   有全面的测试套件，以确保承诺的行为得到维持、重要逻辑正确，并且测试在失败时提供清晰、可操作的诊断

在使用接口和类型这类抽象时（它们按定义会从使用它们的语境中移除信息），重要的是确保它们带来足够的好处。当使用具体类型时，编辑器与 IDE 能直接跳到方法定义并显示相应的文档，否则只能指向接口定义。接口是强大的工具，但有代价：维护者可能需要理解底层实现的细节才能正确使用接口，而这些必须在接口文档中或调用处加以说明。

可维护的代码还避免把重要细节藏在容易被忽略的地方。例如，在下面每一行代码中，单个字符的有无对理解该行至关重要：

```go
// Bad:
// The use of = instead of := can change this line completely.
if user, err = db.UserByID(userID); err != nil {
    // ...
}
```

```go
// Bad:
// The ! in the middle of this line is very easy to miss.
leap := (year%4 == 0) && (!(year%100 == 0) || (year%400 == 0))
```

这两者都并非不正确，但都可以写得更明确，或者配一条注释来提醒注意重要的行为：

```go
// Good:
u, err := db.UserByID(userID)
if err != nil {
    return fmt.Errorf("invalid origin user: %s", err)
}
user = u
```

```go
// Good:
// Gregorian leap years aren't just year%4 == 0.
// See https://en.wikipedia.org/wiki/Leap_year#Algorithm.
var (
    leap4   = year%4 == 0
    leap100 = year%100 == 0
    leap400 = year%400 == 0
)
leap := leap4 && (!leap100 || leap400)
```

同样地，如果一个辅助函数把关键逻辑或重要的边界情况隐藏起来，就很容易让将来的改动没能妥善考虑到它。

可预测的名称是可维护代码的另一个特征。包的使用者或某段代码的维护者应当能够预测在给定语境下变量、方法或函数的名称。相同概念的函数参数名与接收者名通常应当一致，这既能让文档易于理解，也便于以最小的开销重构代码。

可维护的代码尽量减少其依赖（无论隐式还是显式）。依赖更少的包意味着能影响行为的代码行数更少。避免依赖内部或未记录在案的行为，能让代码在这些行为将来发生变化时更不容易带来维护负担。

在考虑如何组织或编写代码时，值得花时间想清楚代码可能随时间演进的方式。如果某种做法更有利于将来更轻松、更安全的改动，那通常是一笔好的取舍，即使它意味着稍微更复杂一点的设计。

<a id="consistency"></a>

#### 一致

一致的代码，是指在更广的代码库中、在团队或包的语境中、乃至在单个文件内，看上去、感觉上和行为上都与相似的代码一致。

一致性的考量并不凌驾于上述任何原则之上，但如果必须打破平局，偏向一致性往往是有益的。

包内的一致往往是最直接重要的一个一致性层次。如果同一个问题在一个包内以多种方式处理，或同一个概念在一个文件里有许多名字，会非常刺眼。不过，即便如此，也不应凌驾于成文的风格原则或全局一致性之上。

<a id="core"></a>

### 核心准则

这些准则汇集了 Go 风格中最重要的方面，所有 Go 代码都应当遵循。我们期望在获得可读性资格时，这些原则都已被学习和遵循。它们不预期会频繁变化，新增内容必须跨过很高的门槛。

以下准则是对 [Effective Go] 中建议的展开，这些建议为整个社区的 Go 代码提供了共同的基准。

[Effective Go]: https://go.dev/doc/effective_go

<a id="formatting"></a>

#### 格式

所有 Go 源文件都必须符合 `gofmt` 工具输出的格式。Google 代码库中的预提交检查会强制执行这一格式。[Generated code] 通常也应当格式化（例如使用 [`format.Source`]），因为它在 Code Search 中也是可浏览的。

[Generated code]: https://docs.bazel.build/versions/main/be/general.html#genrule
[`format.Source`]: https://pkg.go.dev/go/format#Source

<a id="mixed-caps"></a>

#### MixedCaps

在书写由多个单词组成的名称时，Go 源代码使用 `MixedCaps` 或 `mixedCaps`（驼峰式），而不是下划线（蛇形）。

即使这样会打破其他语言中的惯例，也照样适用。例如，常量在导出时写作 `MaxLength`（不是 `MAX_LENGTH`），在未导出时写作 `maxLength`（不是 `max_length`）。

就选择首字母大小写而言，局部变量被视为 [unexported]。

<!--#include file="/go/g3doc/style/includes/special-name-exception.md"-->

[unexported]: https://go.dev/ref/spec#Exported_identifiers

<a id="line-length"></a>

#### 行长

Go 源代码没有固定的行长。如果某一行感觉太长，优先重构而不是把它拆开。如果它已经短到实际可行的程度，就应当允许它保持较长。

不要在以下情况拆分一行：

*   在[缩进变化](go-decisions.md#indentation-confusion)之前（例如函数声明、条件语句）
*   为了让长字符串（例如 URL）塞进多行较短的文本

<a id="naming"></a>

#### 命名

命名与其说是科学，不如说是艺术。在 Go 中，名称往往比其他许多语言稍短，但同样适用 [general guidelines]。名称应当：

*   在使用时不让人觉得[重复冗余](go-decisions.md#repetition)
*   考虑到语境
*   不重复已经清楚的概念

关于命名的更具体指导见[风格决策](go-decisions.md#naming)。

[general guidelines]: https://testing.googleblog.com/2017/10/code-health-identifiernamingpostforworl.html

<a id="local-consistency"></a>

#### 局部一致性

当风格指南对某个具体风格要点没有规定时，作者可以自由选择自己喜欢的风格，除非邻近的代码（通常是同一文件或同一个包内，有时是同一团队或项目目录内）在该问题上已采取了一致的立场。

**有效**的局部风格考量的例子：

*   使用 `%s` 或 `%v` 来格式化打印错误
*   使用带缓冲的通道来代替互斥锁

**无效**的局部风格考量的例子：

*   对代码施加行长限制
*   使用基于断言的测试库

如果局部风格与风格指南不一致，但可读性影响仅限于一个文件，通常会在代码评审中提出，而对这种情况的统一修复会超出所涉 CL 的范围。此时，适合提一个 bug 来跟踪该修复。

如果某项改动会加重已有的风格偏离、把它暴露在更多 API 表面、扩大存在该偏离的文件数量，或引入真正的 bug，那么局部一致性就不再是为新代码违反风格指南的正当理由。在这些情况下，作者适合在同一个 CL 中清理既有代码库、在当前 CL 之前先做一次重构，或者找出至少不会让局部问题变糟的替代方案。
