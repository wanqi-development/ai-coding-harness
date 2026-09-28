# Google Python 风格指南

> 译自 Google 的 [Python Style Guide](https://google.github.io/styleguide/pyguide.html)，
> 原文以 [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/) 许可发布。
> 本文件是其简体中文译本，供编码与审查时查阅：章节编号与层级照原文，
> 代码示例、标识符、类型标注与错误消息一律保留英文原文。
>
> 原文的小节编号跳过了 2.15 与 3.9（目录里就没有），这是上游本身的编号缺口。

<a id="s1-background"></a>
<a id="1-background"></a>

<a id="background"></a>
## 1 背景

Python 是 Google 使用的主要动态语言。这份风格指南是 Python 程序的一份*该做与不该做*清单。

为了帮你正确格式化代码，我们准备了 [Vim 设置文件](https://google.github.io/styleguide/google_python_style.vim)。Emacs 用默认设置即可。

许多团队使用 [Black](https://github.com/psf/black) 或 [Pyink](https://github.com/google/pyink) 自动格式化工具，以免在格式上争论。


<a id="s2-python-language-rules"></a>
<a id="2-python-language-rules"></a>

<a id="python-language-rules"></a>
## 2 Python 语言规则

<a id="s2.1-lint"></a>
<a id="21-lint"></a>

<a id="lint"></a>
### 2.1 Lint

用这份 [pylintrc](https://google.github.io/styleguide/pylintrc) 对你的代码运行 `pylint`。

<a id="s2.1.1-definition"></a>
<a id="211-definition"></a>

<a id="lint-definition"></a>
#### 2.1.1 定义

`pylint` 是一个用于查找 Python 源码中 bug 与风格问题的工具。它能发现那些在 C、C++ 这类动态性较弱的语言里通常由编译器发现的问题。由于 Python 是动态语言，有些警告可能是错的；不过虚假警告应当相当少见。

<a id="s2.1.2-pros"></a>
<a id="212-pros"></a>

<a id="lint-pros"></a>
#### 2.1.2 优点

能抓住容易漏掉的错误，例如拼写错误、在赋值之前使用变量等。

<a id="s2.1.3-cons"></a>
<a id="213-cons"></a>

<a id="lint-cons"></a>
#### 2.1.3 缺点

`pylint` 并不完美。要利用它，有时我们得绕着它写代码、压制它的警告，或者把它修好。

<a id="s2.1.4-decision"></a>
<a id="214-decision"></a>

<a id="lint-decision"></a>
#### 2.1.4 结论

确保对你的代码运行 `pylint`。


如果警告不合适，就把它压制掉，免得其他问题被掩盖。要压制警告，可以写行级注释：

```python
def do_PUT(self):  # WSGI name, so pylint: disable=invalid-name
  ...
```

`pylint` 的每条警告都用符号名标识（如 `empty-docstring`），Google 特有的警告以 `g-` 开头。

如果从符号名看不出压制的理由，就补一句说明。

这样压制的好处是，我们能方便地搜出所有压制处并回头复查。

运行下面的命令可以列出 `pylint` 的警告：

```shell
pylint --list-msgs
```

要查某条消息的更多信息，用：

```shell
pylint --help-msg=invalid-name
```

`pylint: disable` 优于已弃用的旧写法 `pylint: disable-msg`。

未使用参数的警告，可以靠函数开头删除这些变量来压制。一定要加注释说明为什么删它。「Unused.」就够。例如：

```python
def viking_cafe_order(spam: str, beans: str, eggs: str | None = None) -> str:
    del beans, eggs  # Unused by vikings.
    return spam + spam + spam
```

压制这类警告的其他常见做法还有：用 '`_`' 作为未使用参数的标识符，给参数名加 '`unused_`' 前缀，或把它们赋给 '`_`'。这些做法允许但不再鼓励。它们会弄坏按名字传参的调用方，而且并不能保证这些参数真的没被用到。

<a id="s2.2-imports"></a>
<a id="22-imports"></a>

<a id="imports"></a>
### 2.2 导入

`import` 语句只用于包与模块，不用于单个类型、类或函数。

<a id="s2.2.1-definition"></a>
<a id="221-definition"></a>

<a id="imports-definition"></a>
#### 2.2.1 定义

在模块之间共享代码的复用机制。

<a id="s2.2.2-pros"></a>
<a id="222-pros"></a>

<a id="imports-pros"></a>
#### 2.2.2 优点

命名空间的管理约定很简单。每个标识符的来源都以一致的方式标明：`x.Obj` 表示对象 `Obj` 定义在模块 `x` 里。

<a id="s2.2.3-cons"></a>
<a id="223-cons"></a>

<a id="imports-cons"></a>
#### 2.2.3 缺点

模块名仍可能冲突。有些模块名长得不方便。

<a id="s2.2.4-decision"></a>
<a id="224-decision"></a>

<a id="imports-decision"></a>
#### 2.2.4 结论

*   导入包与模块用 `import x`。
*   当 `x` 是包前缀、`y` 是不带前缀的模块名时，用 `from x import y`。
*   出现下列任一情形时，用 `from x import y as z`：
    -   要导入两个都叫 `y` 的模块。
    -   `y` 与当前模块里定义的顶层名字冲突。
    -   `y` 与属于公开 API 的常用参数名冲突（例如 `features`）。
    -   `y` 是个长得不方便的名字。
    -   `y` 在你的代码语境里过于泛泛（例如 `from storage.file_system import options as fs_options`）。
*   只有当 `z` 是标准缩写时才用 `import y as z`（例如 `import numpy as np`）。

例如模块 `sound.effects.echo` 可以这样导入：

```python
from sound.effects import echo
...
echo.EchoFilter(input, output, delay=0.7, atten=4)
```

导入时不要用相对名。即使模块就在同一个包里，也用完整的包名。这有助于避免无意中把同一个包导入两次。

<a id="imports-exemptions"></a>
##### 2.2.4.1 豁免

下列模块中用于支撑静态分析与类型检查的符号，不受本规则约束：

*   [`typing` 模块](#typing-imports)
*   [`collections.abc` 模块](#typing-imports)
*   [`typing_extensions` 模块](https://github.com/python/typing_extensions/blob/main/README.md)

<a id="s2.3-packages"></a>
<a id="23-packages"></a>

<a id="packages"></a>
### 2.3 包

导入每个模块时都用该模块的完整路径位置。

<a id="s2.3.1-pros"></a>
<a id="231-pros"></a>

<a id="packages-pros"></a>
#### 2.3.1 优点

避免模块名冲突，也避免因模块搜索路径与作者预期不符而导错模块。还能让模块更容易找。

<a id="s2.3.2-cons"></a>
<a id="232-cons"></a>

<a id="packages-cons"></a>
#### 2.3.2 缺点

部署代码更麻烦，因为必须复制包层级。有了现代部署机制后，这其实不算问题。

<a id="s2.3.3-decision"></a>
<a id="233-decision"></a>

<a id="packages-decision"></a>
#### 2.3.3 结论

所有新代码都应当用完整的包名导入每个模块。

导入应当写成这样：

```python
Yes:
  # Reference absl.flags in code with the complete name (verbose).
  import absl.flags
  from doctor.who import jodie

  _FOO = absl.flags.DEFINE_string(...)
```

```python
Yes:
  # Reference flags in code with just the module name (common).
  from absl import flags
  from doctor.who import jodie

  _FOO = flags.DEFINE_string(...)
```

*（假定本文件位于 `doctor/who/`，那里同时存在 `jodie.py`）*

```python
No:
  # Unclear what module the author wanted and what will be imported.  The actual
  # import behavior depends on external factors controlling sys.path.
  # Which possible jodie module did the author intend to import?
  import jodie
```

主程序所在的目录不应当被假定在 `sys.path` 里，尽管某些环境确实如此。既然如此，代码就应当假定 `import jodie` 指的是名为 `jodie` 的第三方包或顶层包，而不是本地的 `jodie.py`。


<a id="s2.4-exceptions"></a>
<a id="24-exceptions"></a>

<a id="exceptions"></a>
### 2.4 异常

允许使用异常，但必须谨慎使用。

<a id="s2.4.1-definition"></a>
<a id="241-definition"></a>

<a id="exceptions-definition"></a>
#### 2.4.1 定义

异常是一种跳出正常控制流、去处理错误或其他异常状况的手段。

<a id="s2.4.2-pros"></a>
<a id="242-pros"></a>

<a id="exceptions-pros"></a>
#### 2.4.2 优点

正常流程的代码不会被错误处理代码搅乱。当某个条件发生时，它还能让控制流跳过多个栈帧，例如一步从 N 层嵌套函数里返回，而不必逐层传递错误码。

<a id="s2.4.3-cons"></a>
<a id="243-cons"></a>

<a id="exceptions-cons"></a>
#### 2.4.3 缺点

可能让控制流难以理解。做库调用时容易漏掉出错的情况。

<a id="s2.4.4-decision"></a>
<a id="244-decision"></a>

<a id="exceptions-decision"></a>
#### 2.4.4 结论

异常必须满足若干条件：

-   有意义时就用内置异常类。例如，用 `ValueError` 表示违反前置条件这类编程错误，比如校验函数参数时可能出现的那种。

-   不要用 `assert` 语句代替条件分支或前置条件校验。它们不得对应用逻辑至关重要。一个试金石是：把该 `assert` 删掉也不会弄坏代码。`assert` 条件
    [不保证](https://docs.python.org/3/reference/simple_stmts.html#the-assert-statement)
    会被求值。对基于 [pytest](https://pytest.org) 的测试来说，用 `assert` 校验预期是可以的，也本该如此。例如：

    
    ```python
    Yes:
      def connect_to_next_port(self, minimum: int) -> int:
        """Connects to the next available port.

        Args:
          minimum: A port value greater or equal to 1024.

        Returns:
          The new minimum port.

        Raises:
          ConnectionError: If no available port is found.
        """
        if minimum < 1024:
          # Note that this raising of ValueError is not mentioned in the doc
          # string's "Raises:" section because it is not appropriate to
          # guarantee this specific behavioral reaction to API misuse.
          raise ValueError(f'Min. port must be at least 1024, not {minimum}.')
        port = self._find_next_open_port(minimum)
        if port is None:
          raise ConnectionError(
              f'Could not connect to service on port {minimum} or higher.')
        # The code does not depend on the result of this assert.
        assert port >= minimum, (
            f'Unexpected port {port} when minimum was {minimum}.')
        return port
    ```

    ```python
    No:
      def connect_to_next_port(self, minimum: int) -> int:
        """Connects to the next available port.

        Args:
          minimum: A port value greater or equal to 1024.

        Returns:
          The new minimum port.
        """
        assert minimum >= 1024, 'Minimum port must be at least 1024.'
        # The following code depends on the previous assert.
        port = self._find_next_open_port(minimum)
        assert port is not None
        # The type checking of the return statement relies on the assert.
        return port
    ```


-   库或包可以定义自己的异常。这样做时必须继承已有的异常类。异常名应当以 `Error` 结尾，且不应引入重复（如 `foo.FooError`）。

-   绝不要用包办一切的 `except:` 语句，也不要捕获 `Exception` 或 `StandardError`，除非你是

    -   在重新抛出该异常，或者
    -   在程序里造一个隔离点，让异常不被向上传播，而是被记录下来并压制掉，例如通过守护最外层代码块来保护一个线程不崩溃。

    Python 在这方面非常宽容，`except:` 会真的捕获一切，包括拼错的变量名、`sys.exit()` 调用、Ctrl+C 中断、unittest 失败，以及各种你根本不希望捕获的异常。

-   把 `try`/`except` 块里的代码量降到最低。`try` 的块体越大，就越可能在你没料到会抛异常的那行代码上抛出异常。那种情况下，`try`/`except` 块掩盖了真正的错误。

-   用 `finally` 子句执行无论 `try` 块里是否抛出异常都要执行的代码。这常用于清理，比如关闭文件。

<a id="s2.5-global-variables"></a>
<a id="25-global-variables"></a>
<a id="s2.5-global-state"></a>
<a id="25-global-state"></a>

<a id="global-variables"></a>
### 2.5 可变的全局状态

避免可变的全局状态。

<a id="s2.5.1-definition"></a>
<a id="251-definition"></a>

<a id="global-variables-definition"></a>
#### 2.5.1 定义

模块层的值，或类的属性，且能在程序执行期间被改动。

<a id="s2.5.2-pros"></a>
<a id="252-pros"></a>

<a id="global-variables-pros"></a>
#### 2.5.2 优点

偶尔有用。

<a id="s2.5.3-cons"></a>
<a id="253-cons"></a>

<a id="global-variables-cons"></a>
#### 2.5.3 缺点

*   破坏封装：这种设计会让合理的目标难以达成。例如，若用全局状态管理数据库连接，那么同时连两个不同的数据库（比如迁移期间比对差异）就变得困难。全局注册表也容易引出类似问题。

*   有可能在导入期间改变模块行为，因为对全局变量的赋值发生在模块首次被导入时。

<a id="s2.5.4-decision"></a>
<a id="254-decision"></a>

<a id="global-variables-decision"></a>
#### 2.5.4 结论

避免可变的全局状态。

在少数确实需要全局状态的情况下，可变的全局实体应当声明在模块层，或作为类的属性，并在名字前加 `_` 使其成为内部实现。必要时，对可变全局状态的外部访问必须经由公开函数或类方法。见下文的[命名](#s3.16-naming)。请在注释里、或注释所链接的文档里解释使用可变全局状态的设计理由。

模块层常量是允许且鼓励的。例如：内部使用的常量写 `_MAX_HOLY_HANDGRENADE_COUNT = 3`，公开 API 常量写 `SIR_LANCELOTS_FAVORITE_COLOR = "blue"`。常量必须用全大写加下划线命名。见下文的[命名](#s3.16-naming)。

<a id="s2.6-nested"></a>
<a id="26-nested"></a>

<a id="nested-classes-functions"></a>
### 2.6 嵌套/局部/内部类与函数

嵌套的局部函数或类用来闭包一个局部变量时没问题。内部类也没问题。

<a id="s2.6.1-definition"></a>
<a id="261-definition"></a>

<a id="nested-classes-functions-definition"></a>
#### 2.6.1 定义

类可以定义在方法、函数或类的内部。函数可以定义在方法或函数的内部。嵌套函数对外层作用域里定义的变量只有只读访问权。

<a id="s2.6.2-pros"></a>
<a id="262-pros"></a>

<a id="nested-classes-functions-pros"></a>
#### 2.6.2 优点

允许定义只在极小范围内使用的工具类与工具函数。很符合 [ADT](https://en.wikipedia.org/wiki/Abstract_data_type) 的味道。常用于实现装饰器。

<a id="s2.6.3-cons"></a>
<a id="263-cons"></a>

<a id="nested-classes-functions-cons"></a>
#### 2.6.3 缺点

嵌套函数与类无法直接测试。嵌套会让外层函数变长、可读性变差。

<a id="s2.6.4-decision"></a>
<a id="264-decision"></a>

<a id="nested-classes-functions-decision"></a>
#### 2.6.4 结论

可以用，但有些注意事项。除了闭包 `self` 或 `cls` 之外的局部值之外，避免嵌套函数或类。不要只为把函数藏起来不给模块使用者看就嵌套它。改为在模块层给它的名字加 `_` 前缀，这样测试仍然能访问到它。

<a id="s2.7-comprehensions"></a>
<a id="s2.7-list_comprehensions"></a>
<a id="27-list_comprehensions"></a>
<a id="list_comprehensions"></a>
<a id="list-comprehensions"></a>

<a id="comprehensions"></a>
### 2.7 推导式与生成器表达式

简单情形下可以用。

<a id="s2.7.1-definition"></a>
<a id="271-definition"></a>

<a id="comprehensions-definition"></a>
#### 2.7.1 定义

列表、字典、集合推导式以及生成器表达式，提供了一种简洁高效的方式，用来创建容器类型与迭代器，而不必动用传统循环、`map()`、`filter()` 或 `lambda`。

<a id="s2.7.2-pros"></a>
<a id="272-pros"></a>

<a id="comprehensions-pros"></a>
#### 2.7.2 优点

简单的推导式可以比其他创建字典、列表或集合的手法更清楚、更简单。生成器表达式可以非常高效，因为它完全省掉了列表的创建。

<a id="s2.7.3-cons"></a>
<a id="273-cons"></a>

<a id="comprehensions-cons"></a>
#### 2.7.3 缺点

复杂的推导式或生成器表达式会很难读。

<a id="s2.7.4-decision"></a>
<a id="274-decision"></a>

<a id="comprehensions-decision"></a>
#### 2.7.4 结论

推导式允许使用，但不允许多个 `for` 子句或过滤表达式。优先照顾可读性，而不是简洁。

```python
Yes:
  result = [mapping_expr for value in iterable if filter_expr]

  result = [
      is_valid(metric={'key': value})
      for value in interesting_iterable
      if a_longer_filter_expression(value)
  ]

  descriptive_name = [
      transform({'key': key, 'value': value}, color='black')
      for key, value in generate_iterable(some_input)
      if complicated_condition_is_met(key, value)
  ]

  result = []
  for x in range(10):
    for y in range(5):
      if x * y > 10:
        result.append((x, y))

  return {
      x: complicated_transform(x)
      for x in long_generator_function(parameter)
      if x is not None
  }

  return (x**2 for x in range(10))

  unique_names = {user.name for user in users if user is not None}
```

```python
No:
  result = [(x, y) for x in range(10) for y in range(5) if x * y > 10]

  return (
      (x, y, z)
      for x in range(5)
      for y in range(5)
      if x != y
      for z in range(5)
      if y != z
  )
```

<a id="s2.8-default-iterators-and-operators"></a>

<a id="default-iterators-operators"></a>
### 2.8 默认迭代器与运算符

对支持它们的类型（如列表、字典、文件）使用默认的迭代器与运算符。

<a id="s2.8.1-definition"></a>
<a id="281-definition"></a>

<a id="default-iterators-operators-definition"></a>
#### 2.8.1 定义

容器类型（如字典与列表）定义了默认的迭代器与成员测试运算符（"in" 与 "not in"）。

<a id="s2.8.2-pros"></a>
<a id="282-pros"></a>

<a id="default-iterators-operators-pros"></a>
#### 2.8.2 优点

默认的迭代器与运算符既简单又高效。它们直接表达操作本身，不需要额外的方法调用。使用默认运算符的函数是泛型的，任何支持该操作的类型都能用。

<a id="s2.8.3-cons"></a>
<a id="283-cons"></a>

<a id="default-iterators-operators-cons"></a>
#### 2.8.3 缺点

光看方法名看不出对象的类型（除非变量有类型标注）。不过这同时也是个优点。

<a id="s2.8.4-decision"></a>
<a id="284-decision"></a>

<a id="default-iterators-operators-decision"></a>
#### 2.8.4 结论

对支持它们的类型（如列表、字典、文件）使用默认的迭代器与运算符。内置类型也定义了迭代器方法。优先用这些方法，而不是返回列表的方法；但不要在迭代容器的同时改动它。

```python
Yes:  for key in adict: ...
      if obj in alist: ...
      for line in afile: ...
      for k, v in adict.items(): ...
```

```python
No:   for key in adict.keys(): ...
      for line in afile.readlines(): ...
```

<a id="s2.9-generators"></a>
<a id="29-generators"></a>

<a id="generators"></a>
### 2.9 生成器

按需使用生成器。

<a id="s2.9.1-definition"></a>
<a id="291-definition"></a>

<a id="generators-definition"></a>
#### 2.9.1 定义

生成器函数返回一个迭代器，它每次执行 yield 语句时产出一个值。产出一个值之后，生成器函数的运行时状态被挂起，直到需要下一个值为止。

<a id="s2.9.2-pros"></a>
<a id="292-pros"></a>

<a id="generators-pros"></a>
#### 2.9.2 优点

代码更简单，因为每次调用时局部变量与控制流的状态都被保留下来。比起一次创建完整值列表的函数，生成器占用的内存更少。

<a id="s2.9.3-cons"></a>
<a id="293-cons"></a>

<a id="generators-cons"></a>
#### 2.9.3 缺点

生成器里的局部变量不会被垃圾回收，直到该生成器被耗尽、或它自身被垃圾回收为止。

<a id="s2.9.4-decision"></a>
<a id="294-decision"></a>

<a id="generators-decision"></a>
#### 2.9.4 结论

没问题。生成器函数的 docstring 里用 "Yields:" 而不是 "Returns:"。

如果生成器管理着昂贵的资源，务必强制完成清理。

一个做清理的好办法是用上下文管理器把生成器包起来，见 [PEP-0533](https://peps.python.org/pep-0533/)。

<a id="s2.10-lambda-functions"></a>
<a id="210-lambda-functions"></a>

<a id="lambdas"></a>
### 2.10 Lambda 函数

单行时可以用。优先用生成器表达式，而不是配 `lambda` 的 `map()` 或 `filter()`。

<a id="s2.10.1-definition"></a>
<a id="2101-definition"></a>

<a id="lambdas-definition"></a>
#### 2.10.1 定义

Lambda 在表达式里定义匿名函数，而不是在语句里。

<a id="s2.10.2-pros"></a>
<a id="2102-pros"></a>

<a id="lambdas-pros"></a>
#### 2.10.2 优点

方便。

<a id="s2.10.3-cons"></a>
<a id="2103-cons"></a>

<a id="lambdas-cons"></a>
#### 2.10.3 缺点

比局部函数更难读、更难调试。没有名字意味着堆栈更难理解。表达能力也受限，因为函数体只能是一个表达式。

<a id="s2.10.4-decision"></a>
<a id="2104-decision"></a>

<a id="lambdas-decision"></a>
#### 2.10.4 结论

允许使用 Lambda。如果 lambda 函数里的代码跨多行、或超过 60–80 个字符，那不如把它定义成普通的[嵌套函数](#lexical-scoping)。

对于乘法这类常见操作，请用 `operator` 模块里的函数，而不是 lambda 函数。例如用 `operator.mul`，而不是 `lambda x, y: x * y`。

<a id="s2.11-conditional-expressions"></a>
<a id="211-conditional-expressions"></a>

<a id="conditional-expressions"></a>
### 2.11 条件表达式

简单情形下可以用。

<a id="s2.11.1-definition"></a>
<a id="2111-definition"></a>

<a id="conditional-expressions-definition"></a>
#### 2.11.1 定义

条件表达式（有时也叫「三元运算符」）是一种为 if 语句提供更短写法的机制。例如：`x = 1 if cond else 2`。

<a id="s2.11.2-pros"></a>
<a id="2112-pros"></a>

<a id="conditional-expressions-pros"></a>
#### 2.11.2 优点

比 if 语句更短、更方便。

<a id="s2.11.3-cons"></a>
<a id="2113-cons"></a>

<a id="conditional-expressions-cons"></a>
#### 2.11.3 缺点

可能比 if 语句更难读。表达式一长，条件就不好找。

<a id="s2.11.4-decision"></a>
<a id="2114-decision"></a>

<a id="conditional-expressions-decision"></a>
#### 2.11.4 结论

简单情形下可以用。三部分——真表达式、if 表达式、else 表达式——必须各占一行内放得下。事情一复杂，就用完整的 if 语句。

```python
Yes:
    one_line = 'yes' if predicate(value) else 'no'
    slightly_split = ('yes' if predicate(value)
                      else 'no, nein, nyet')
    the_longest_ternary_style_that_can_be_done = (
        'yes, true, affirmative, confirmed, correct'
        if predicate(value)
        else 'no, false, negative, nay')
```

```python
No:
    bad_line_breaking = ('yes' if predicate(value) else
                         'no')
    portion_too_long = ('yes'
                        if some_long_module.some_long_predicate_function(
                            really_long_variable_name)
                        else 'no, false, negative, nay')
```

<a id="s2.12-default-argument-values"></a>
<a id="212-default-argument-values"></a>

<a id="default-arguments"></a>
### 2.12 默认参数值

多数情况下可以用。

<a id="s2.12.1-definition"></a>
<a id="2121-definition"></a>

<a id="default-arguments-definition"></a>
#### 2.12.1 定义

你可以在函数参数列表末尾给变量指定值，例如 `def foo(a, b=0):`。若调用 `foo` 时只传一个参数，`b` 就设为 0。若传两个参数，`b` 就取第二个参数的值。

<a id="s2.12.2-pros"></a>
<a id="2122-pros"></a>

<a id="default-arguments-pros"></a>
#### 2.12.2 优点

你常会写一个用了很多默认值的函数，但偶尔又想覆盖这些默认值。默认参数值提供了一个方便的办法，不必为这些罕见情形定义一大堆函数。又因为 Python 不支持重载的方法/函数，默认参数是「假装」重载行为的一个简便手段。

<a id="s2.12.3-cons"></a>
<a id="2123-cons"></a>

<a id="default-arguments-cons"></a>
#### 2.12.3 缺点

默认参数在模块加载时只求值一次。如果参数是列表或字典这类可变对象，这可能出问题。若函数改动了这个对象（例如往列表里追加一项），默认值就被改掉了。

<a id="s2.12.4-decision"></a>
<a id="2124-decision"></a>

<a id="default-arguments-decision"></a>
#### 2.12.4 结论

可以用，但有一个注意事项：

不要在函数或方法定义里用可变对象作默认值。

```python
Yes: def foo(a, b=None):
         if b is None:
             b = []
Yes: def foo(a, b: Sequence | None = None):
         if b is None:
             b = []
Yes: def foo(a, b: Sequence = ()):  # Empty tuple OK since tuples are immutable.
         ...
```

```python
from absl import flags
_FOO = flags.DEFINE_string(...)

No:  def foo(a, b=[]):
         ...
No:  def foo(a, b=time.time()):  # Is `b` supposed to represent when this module was loaded?
         ...
No:  def foo(a, b=_FOO.value):  # sys.argv has not yet been parsed...
         ...
No:  def foo(a, b: Mapping = {}):  # Could still get passed to unchecked code.
         ...
```

<a id="s2.13-properties"></a>
<a id="213-properties"></a>

<a id="properties"></a>
### 2.13 属性（Properties）

属性可以用来控制那些需要做平凡计算或逻辑的属性读写。属性的实现必须符合常规属性访问的一般预期：开销小、直截了当、不出人意料。

<a id="s2.13.1-definition"></a>
<a id="2131-definition"></a>

<a id="properties-definition"></a>
#### 2.13.1 定义

一种把「获取与设置属性」的方法调用包装成标准属性访问的方式。

<a id="s2.13.2-pros"></a>
<a id="2132-pros"></a>

<a id="properties-pros"></a>
#### 2.13.2 优点

*   提供属性访问与赋值的 API，而不是[取值器与设值器](#getters-and-setters)的方法调用。
*   可以用来让属性只读。
*   可以让计算变成惰性的。
*   当类的内部实现独立于类使用者而演进时，提供了一种维持类公开接口的办法。

<a id="s2.13.3-cons"></a>
<a id="2133-cons"></a>

<a id="properties-cons"></a>
#### 2.13.3 缺点

*   和运算符重载一样，可能藏起副作用。
*   可能让子类困惑。

<a id="s2.13.4-decision"></a>
<a id="2134-decision"></a>

<a id="properties-decision"></a>
#### 2.13.4 结论

属性允许使用，但和运算符重载一样，只应在必要时使用，并且要符合典型属性访问的预期；否则请遵循[取值器与设值器](#getters-and-setters)的规则。

例如，用属性只是简单地读取并设置一个内部属性是不允许的：这里没有任何计算发生，所以该属性是多余的（[直接把该属性公开就行](#getters-and-setters)）。相比之下，用属性来控制属性访问、或计算一个*平凡*派生值则是允许的：逻辑简单，也不出人意料。

属性应当用 `@property` [装饰器](#s2.17-function-and-method-decorators)创建。手工实现属性描述符被视为一种[高级特性](#power-features)。

属性的继承可能不直观。不要用属性去实现子类日后可能想要覆盖并扩展的计算。

<a id="s2.14-truefalse-evaluations"></a>
<a id="214-truefalse-evaluations"></a>

<a id="truefalse-evaluations"></a>
### 2.14 真/假值求值

尽可能用「隐式」假值（有几条注意事项）。

<a id="s2.14.1-definition"></a>
<a id="2141-definition"></a>

<a id="truefalse-evaluations-definition"></a>
#### 2.14.1 定义

Python 在布尔语境里会把某些值判为 `False`。一条快速的「经验法则」是：所有「空」值都算假，所以 `0, None, [], {}, ''` 在布尔语境里都求值为假。

<a id="s2.14.2-pros"></a>
<a id="2142-pros"></a>

<a id="truefalse-evaluations-pros"></a>
#### 2.14.2 优点

用 Python 布尔值的条件更好读，也更不容易出错。多数情况下还更快。

<a id="s2.14.3-cons"></a>
<a id="2143-cons"></a>

<a id="truefalse-evaluations-cons"></a>
#### 2.14.3 缺点

在 C/C++ 开发者看来可能有点怪。

<a id="s2.14.4-decision"></a>
<a id="2144-decision"></a>

<a id="truefalse-evaluations-decision"></a>
#### 2.14.4 结论

尽可能用「隐式」假值，例如用 `if foo:` 而不是 `if foo != []:`。不过有几条注意事项要记在心里：

-   检查 `None` 值一律用 `if foo is None:`（或 `is not None`）。例如，当要测某个默认为 `None` 的变量或参数是否被设成了别的值时。那个别的值可能恰好是个在布尔语境里为假的值！

-   绝不要用 `==` 把布尔变量与 `False` 比较。改用 `if not x:`。如果需要把 `False` 与 `None` 区分开，就把表达式串起来，例如 `if not x and x is not None:`。

-   对序列（字符串、列表、元组）来说，利用「空序列为假」这一点，所以 `if seq:` 与 `if not seq:` 分别优于 `if len(seq):` 与 `if not len(seq):`。

-   处理整数时，隐式假值可能风险大于收益（比如把 `None` 误当成 0）。你可以拿一个已知是整数（且不是 `len()` 的结果）的值与整数 0 比较。

    ```python
    Yes: if not users:
             print('no users')

         if i % 10 == 0:
             self.handle_multiple_of_ten()

         def f(x=None):
             if x is None:
                 x = []
    ```

    ```python
    No:  if len(users) == 0:
             print('no users')

         if not i % 10:
             self.handle_multiple_of_ten()

         def f(x=None):
             x = x or []
    ```

-   注意 `'0'`（即字符串形式的 `0`）求值为真。

-   注意 Numpy 数组在隐式布尔语境里可能抛异常。判断 `np.array` 是否为空时优先用 `.size` 属性（例如 `if not users.size`）。

<a id="s2.16-lexical-scoping"></a>
<a id="216-lexical-scoping"></a>

<a id="lexical-scoping"></a>
### 2.16 词法作用域

可以用。

<a id="s2.16.1-definition"></a>
<a id="2161-definition"></a>

<a id="lexical-scoping-definition"></a>
#### 2.16.1 定义

嵌套的 Python 函数可以引用外层函数里定义的变量，但不能给它们赋值。变量绑定按词法作用域解析，也就是说，依据的是静态的程序文本。块里对某个名字的任何赋值，都会让 Python 把该名字的所有引用都当成局部变量，即使那些引用出现在赋值之前。若出现了 global 声明，该名字就被当作全局变量。

这个特性的一个用法示例如下：

```python
def get_adder(summand1: float) -> Callable[[float], float]:
    """Returns a function that adds numbers to a given number."""
    def adder(summand2: float) -> float:
        return summand1 + summand2

    return adder
```

<a id="s2.16.2-pros"></a>
<a id="2162-pros"></a>

<a id="lexical-scoping-pros"></a>
#### 2.16.2 优点

常常带来更清楚、更优雅的代码。对熟悉 Lisp 与 Scheme（以及 Haskell、ML 等等）的程序员来说尤其舒服。

<a id="s2.16.3-cons"></a>
<a id="2163-cons"></a>

<a id="lexical-scoping-cons"></a>
#### 2.16.3 缺点

可能引出令人困惑的 bug，例如这个基于 [PEP-0227](https://peps.python.org/pep-0227/) 的例子：

```python
i = 4
def foo(x: Iterable[int]):
    def bar():
        print(i, end='')
    # ...
    # A bunch of code here
    # ...
    for i in x:  # Ah, i *is* local to foo, so this is what bar sees
        print(i, end='')
    bar()
```

所以 `foo([1, 2, 3])` 会打印 `1 2 3 3`，而不是 `1 2 3 4`。

<a id="s2.16.4-decision"></a>
<a id="2164-decision"></a>

<a id="lexical-scoping-decision"></a>
#### 2.16.4 结论

可以用。

<a id="s2.17-function-and-method-decorators"></a>
<a id="217-function-and-method-decorators"></a>
<a id="function-and-method-decorators"></a>

<a id="decorators"></a>
### 2.17 函数与方法的装饰器

有明确好处时才审慎地使用装饰器。避免使用 `staticmethod`，并限制 `classmethod` 的使用。

<a id="s2.17.1-definition"></a>
<a id="2171-definition"></a>

<a id="decorators-definition"></a>
#### 2.17.1 定义

[用于函数与方法的装饰器](https://docs.python.org/3/glossary.html#term-decorator)（也就是「`@` 记法」）。一个常见的装饰器是 `@property`，用于把普通方法转成动态计算的属性。不过装饰器语法也允许自定义装饰器。具体来说，对某个函数 `my_decorator`，下面这段：

```python
class C:
    @my_decorator
    def method(self):
        # method body ...
```

等价于：

```python
class C:
    def method(self):
        # method body ...
    method = my_decorator(method)
```

<a id="s2.17.2-pros"></a>
<a id="2172-pros"></a>

<a id="decorators-pros"></a>
#### 2.17.2 优点

优雅地指明了对方法所做的某种变换；这种变换可以消掉一些重复代码、强制不变量等。

<a id="s2.17.3-cons"></a>
<a id="2173-cons"></a>

<a id="decorators-cons"></a>
#### 2.17.3 缺点

装饰器可以对函数的参数或返回值做任意操作，从而引出出人意料的隐式行为。此外，装饰器在对象定义时执行。对模块层的对象（类、模块级函数等等）来说，这发生在导入时。装饰器代码一旦失败，几乎不可能挽回。

<a id="s2.17.4-decision"></a>
<a id="2174-decision"></a>

<a id="decorators-decision"></a>
#### 2.17.4 结论

有明确好处时才审慎地使用装饰器。装饰器应当遵循与函数相同的导入与命名准则。装饰器的 docstring 应当清楚说明该函数是个装饰器。要为装饰器写单元测试。

装饰器自身要避免外部依赖（例如不要依赖文件、socket、数据库连接等），因为装饰器运行时（在导入时，可能是由 `pydoc` 或其他工具触发的）它们未必可用。一个以合法参数被调用的装饰器，应当（尽可能）保证在所有情况下都能成功。

装饰器是「顶层代码」的一个特例——更多讨论见 [main](#s3.17-main)。

绝不要用 `staticmethod`，除非为了与既有库中定义的 API 集成而不得不如此。改为写一个模块层函数。

只有在写具名构造函数、或写会改动进程级缓存这类必要全局状态的类专属例程时，才用 `classmethod`。

<a id="s2.18-threading"></a>
<a id="218-threading"></a>

<a id="threading"></a>
### 2.18 线程

不要依赖内置类型的原子性。

Python 的内置数据类型（如字典）看上去具有原子操作，但在一些边角情形下它们并不原子（例如 `__hash__` 或 `__eq__` 被实现为 Python 方法时），所以不应当依赖它们的原子性。也不要依赖变量赋值的原子性（因为它又依赖字典）。

线程之间传递数据的首选方式是使用 `queue` 模块的 `Queue` 数据类型。否则就用 `threading` 模块及其锁原语。优先用条件变量与 `threading.Condition`，而不是更低层的锁。

<a id="s2.19-power-features"></a>
<a id="219-power-features"></a>

<a id="power-features"></a>
### 2.19 高级特性

避免这些特性。

<a id="s2.19.1-definition"></a>
<a id="2191-definition"></a>

<a id="power-features-definition"></a>
#### 2.19.1 定义

Python 是一门极其灵活的语言，给了你许多花哨的特性，例如自定义元类、访问字节码、即时编译、动态继承、对象重挂父类、导入黑魔法、反射（例如 `getattr()` 的某些用法）、改动系统内部结构、实现自定义清理的 `__del__` 方法等等。

<a id="s2.19.2-pros"></a>
<a id="2192-pros"></a>

<a id="power-features-pros"></a>
#### 2.19.2 优点

这些都是强大的语言特性。它们能让代码更紧凑。

<a id="s2.19.3-cons"></a>
<a id="2193-cons"></a>

<a id="power-features-cons"></a>
#### 2.19.3 缺点

在这些「酷炫」特性并非绝对必要时，人很容易忍不住去用。底层用了不寻常特性的代码，更难读、更难理解、更难调试。在原作者看来起初不是这样，但回头看代码时，它往往比更长但直截了当的代码更难对付。

<a id="s2.19.4-decision"></a>
<a id="2194-decision"></a>

<a id="power-features-decision"></a>
#### 2.19.4 结论

在你的代码里避免这些特性。

内部使用了这些特性的标准库模块与类是可以用的（例如 `abc.ABCMeta`、`dataclasses` 与 `enum`）。

<a id="s2.20-modern-python"></a>
<a id="220-modern-python"></a>

<a id="modern-python"></a>
### 2.20 现代 Python：`__future__` 导入

新的语言版本在语义上的改动，可能藏在一条特殊的 future 导入之后，以便在较早的运行时里按文件逐一声明启用。

<a id="s2.20.1-definition"></a>
<a id="2201-definition"></a>

<a id="modern-python-definition"></a>
#### 2.20.1 定义

能够通过 `from __future__ import` 语句开启某些较现代的特性，就能提前用上预期未来 Python 版本里的特性。

<a id="s2.20.2-pros"></a>
<a id="2202-pros"></a>

<a id="modern-python-pros"></a>
#### 2.20.2 优点

事实证明，这能让运行时版本升级更平滑：可以在按文件逐一声明兼容性、并防止这些文件内出现回归的同时，逐一改造。现代代码更易维护，因为它更不容易积累那些在日后运行时升级时会变麻烦的技术债。

<a id="s2.20.3-cons"></a>
<a id="2203-cons"></a>

<a id="modern-python-cons"></a>
#### 2.20.3 缺点

在引入所需 future 语句之前的很老的解释器版本上，这类代码可能跑不起来。在支持极其多样化环境的项目里，这种需求更常见。

<a id="s2.20.4-decision"></a>
<a id="2204-decision"></a>

<a id="modern-python-decision"></a>
#### 2.20.4 结论

##### `__future__` 导入

鼓励使用 `from __future__ import` 语句。它让某个源文件今天就能开始使用更现代的 Python 语法特性。一旦你不再需要跑在那些把该特性藏在 `__future__` 导入之后的版本上，就可以放心删掉这些行。

在可能运行于 3.5 这样老、而不是 >= 3.7 的版本上的代码里，导入：

```python
from __future__ import generator_stop
```

更多信息请读 [Python future 语句定义](https://docs.python.org/3/library/__future__.html)文档。

在你确信代码只会在足够现代的环境里使用之前，请不要删掉这些导入。即使你今天还没用到某条 future 导入所开启的特性，把它留在文件里也能防止代码日后的改动无意中依赖旧行为。

其他 `from __future__` 导入语句，按你觉得合适的方式使用。

<a id="s2.21-type-annotated-code"></a>
<a id="s2.21-typed-code"></a>
<a id="221-type-annotated-code"></a>
<a id="typed-code"></a>

<a id="typed-code"></a>
### 2.21 带类型标注的代码

你可以用[类型提示](https://docs.python.org/3/library/typing.html)标注 Python 代码。用 [pytype](https://github.com/google/pytype) 这类类型检查工具在构建时检查代码。多数情况下，只要可行，类型标注就写在源文件里。对第三方模块或扩展模块，标注可以放在[存根 `.pyi` 文件](https://peps.python.org/pep-0484/#stub-files)里。


<a id="s2.21.1-definition"></a>
<a id="2211-definition"></a>

<a id="typed-code-definition"></a>
#### 2.21.1 定义

类型标注（或「类型提示」）用于函数或方法的参数与返回值：

```python
def func(a: int) -> list[int]:
```

你也可以用类似的语法声明变量的类型：

```python
a: SomeType = some_func()
```

<a id="s2.21.2-pros"></a>
<a id="2212-pros"></a>

<a id="typed-code-pros"></a>
#### 2.21.2 优点

类型标注提升代码的可读性与可维护性。类型检查器会把许多运行时错误转成构建时错误，并削弱你使用[高级特性](#power-features)的能力。

<a id="s2.21.3-cons"></a>
<a id="2213-cons"></a>

<a id="typed-code-cons"></a>
#### 2.21.3 缺点

你必须让类型声明保持最新。你可能会看到一些你认为代码没问题的类型错误。使用[类型检查器](https://github.com/google/pytype)可能削弱你使用[高级特性](#power-features)的能力。

<a id="s2.21.4-decision"></a>
<a id="2214-decision"></a>

<a id="typed-code-decision"></a>
#### 2.21.4 结论

强烈建议你在改动代码时启用 Python 类型分析。添加或修改公开 API 时，要把类型标注补上，并在构建系统里启用 pytype 检查。由于静态分析对 Python 来说相对较新，我们承认一些不理想的副作用（例如推断出的类型有误）可能让某些项目无法采用它。那些情况下，建议作者在 BUILD 文件或代码本身里，按合适的位置加一条带 TODO 的注释，或链到描述当前阻碍类型标注采用的问题的 bug。

<a id="s3-python-style-rules"></a>
<a id="3-python-style-rules"></a>

<a id="python-style-rules"></a>
## 3 Python 风格规则

<a id="s3.1-semicolons"></a>
<a id="31-semicolons"></a>

<a id="semicolons"></a>
### 3.1 分号

不要用分号结束语句，也不要用分号把两条语句放在同一行。

<a id="s3.2-line-length"></a>
<a id="32-line-length"></a>

<a id="line-length"></a>
### 3.2 行长

最大行长是*80 个字符*。

对 80 字符上限的明确豁免：

-   很长的 import 语句。
-   注释里的 URL、路径名或长标志。
-   不含空白、拆行会很别扭的长字符串模块级常量，例如 URL 或路径名。
    -   Pylint 压制注释。（例如：`# pylint: disable=invalid-name`）

不要用反斜杠做[显式续行](https://docs.python.org/3/reference/lexical_analysis.html#explicit-line-joining)。

改用 Python 的[圆括号、方括号与花括号内的隐式续行](http://docs.python.org/reference/lexical_analysis.html#implicit-line-joining)。必要时，可以在表达式外面再套一对圆括号。

注意这条规则并不禁止字符串里的反斜杠转义换行（见[下文](#strings)）。

```python
Yes: foo_bar(self, width, height, color='black', design=None, x='foo',
             emphasis=None, highlight=0)
```

```python

Yes: if (width == 0 and height == 0 and
         color == 'red' and emphasis == 'strong'):

     (bridge_questions.clarification_on
      .average_airspeed_of.unladen_swallow) = 'African or European?'

     with (
         very_long_first_expression_function() as spam,
         very_long_second_expression_function() as beans,
         third_thing() as eggs,
     ):
       place_order(eggs, beans, spam, beans)
```

```python

No:  if width == 0 and height == 0 and \
         color == 'red' and emphasis == 'strong':

     bridge_questions.clarification_on \
         .average_airspeed_of.unladen_swallow = 'African or European?'

     with very_long_first_expression_function() as spam, \
           very_long_second_expression_function() as beans, \
           third_thing() as eggs:
       place_order(eggs, beans, spam, beans)
```

当一个字符串字面量放不进单行时，用圆括号做隐式续行。

```python
x = ('This will build a very long long '
     'long long long long long long string')
```

尽量在尽可能高的语法层级断行。如果非得断两次，两次都断在同一语法层级。

```python
Yes: bridgekeeper.answer(
         name="Arthur", quest=questlib.find(owner="Arthur", perilous=True))

     answer = (a_long_line().of_chained_methods()
               .that_eventually_provides().an_answer())

     if (
         config is None
         or 'editor.language' not in config
         or config['editor.language'].use_spaces is False
     ):
       use_tabs()
```

```python
No: bridgekeeper.answer(name="Arthur", quest=questlib.find(
        owner="Arthur", perilous=True))

    answer = a_long_line().of_chained_methods().that_eventually_provides(
        ).an_answer()

    if (config is None or 'editor.language' not in config or config[
        'editor.language'].use_spaces is False):
      use_tabs()

```

注释里如果 URL 很长，必要时让它独占一行。

```python
Yes:  # See details at
      # http://www.example.com/us/developer/documentation/api/content/v2.0/csv_file_name_extension_full_specification.html
```

```python
No:  # See details at
     # http://www.example.com/us/developer/documentation/api/content/\
     # v2.0/csv_file_name_extension_full_specification.html
```

注意上面续行示例中各元素的缩进；解释见[缩进](#s3.4-indentation)一节。

[docstring](#docstrings) 的摘要行必须保持在 80 字符限制之内。

其余情况下，若某行超过 80 字符，而 [Black](https://github.com/psf/black) 或 [Pyink](https://github.com/google/pyink) 自动格式化工具也帮不上忙、没能把该行压到限制以下，那么这一行可以超过上限。作者觉得合适时，鼓励按上面的要点手动断行。

<a id="s3.3-parentheses"></a>
<a id="33-parentheses"></a>

<a id="parentheses"></a>
### 3.3 括号

少用括号。

在元组外面加圆括号可以，但不强求。不要在 return 语句或条件语句里用它们，除非是为了隐式续行、或者要表示一个元组。

```python
Yes: if foo:
         bar()
     while x:
         x = bar()
     if x and y:
         bar()
     if not x:
         bar()
     # For a 1 item tuple the ()s are more visually obvious than the comma.
     onesie = (foo,)
     return foo
     return spam, beans
     return (spam, beans)
     for (x, y) in dict.items(): ...
```

```python
No:  if (x):
         bar()
     if not(x):
         bar()
     return (foo)
```

<a id="s3.4-indentation"></a>
<a id="34-indentation"></a>

<a id="indentation"></a>
### 3.4 缩进

代码块用*4 个空格*缩进。

绝不要用制表符。隐式续行时，折行元素应当垂直对齐（见[行长示例](#s3.2-line-length)），或者用悬挂式的 4 空格缩进。闭合的圆括号、方括号或花括号可以放在表达式末尾，也可以单独成行，但单独成行时应与对应开括号所在行缩进相同。

```python
Yes:   # Aligned with opening delimiter.
       foo = long_function_name(var_one, var_two,
                                var_three, var_four)
       meal = (spam,
               beans)

       # Aligned with opening delimiter in a dictionary.
       foo = {
           'long_dictionary_key': value1 +
                                  value2,
           ...
       }

       # 4-space hanging indent; nothing on first line.
       foo = long_function_name(
           var_one, var_two, var_three,
           var_four)
       meal = (
           spam,
           beans)

       # 4-space hanging indent; nothing on first line,
       # closing parenthesis on a new line.
       foo = long_function_name(
           var_one, var_two, var_three,
           var_four
       )
       meal = (
           spam,
           beans,
       )

       # 4-space hanging indent in a dictionary.
       foo = {
           'long_dictionary_key':
               long_dictionary_value,
           ...
       }
```

```python
No:    # Stuff on first line forbidden.
       foo = long_function_name(var_one, var_two,
           var_three, var_four)
       meal = (spam,
           beans)

       # 2-space hanging indent forbidden.
       foo = long_function_name(
         var_one, var_two, var_three,
         var_four)

       # No hanging indent in a dictionary.
       foo = {
           'long_dictionary_key':
           long_dictionary_value,
           ...
       }
```

<a id="s3.4.1-trailing-comma"></a>
<a id="s3.4.1-trailing-commas"></a>
<a id="s3.4.1-trailing_comma"></a>
<a id="s3.4.1-trailing_commas"></a>
<a id="341-trailing_comma"></a>
<a id="341-trailing_commas"></a>
<a id="trailing_comma"></a>
<a id="trailing_commas"></a>

<a id="trailing-comma"></a>
#### 3.4.1 元素序列里的尾随逗号？

只有当容器的闭合记号 `]`、`)` 或 `}` 不与最后一个元素同行时，以及单元素元组，才推荐在元素序列末尾加尾随逗号。尾随逗号还有一个作用：当最后一个元素后的 `,` 存在时，它是给 Python 自动格式化工具 [Black](https://github.com/psf/black) 或 [Pyink](https://github.com/google/pyink) 的信号，让它把该容器自动格式化成每行一项。

```python
Yes:   golomb3 = [0, 1, 3]
       golomb4 = [
           0,
           1,
           4,
           6,
       ]
```

```python
No:    golomb4 = [
           0,
           1,
           4,
           6,]
```

<a id="s3.5-blank-lines"></a>
<a id="35-blank-lines"></a>

<a id="blank-lines"></a>
### 3.5 空行

顶层定义之间空两行，无论函数还是类。方法定义之间空一行，`class` 的 docstring 与第一个方法之间也空一行。`def` 行之后不空行。函数或方法体内，你觉得合适就用单个空行分隔。

空行不必紧贴定义。例如，紧挨在函数、类、方法定义之前的相关注释是有意义的。也不妨考虑一下，你这句注释作为 docstring 的一部分是否更有用。

<a id="s3.6-whitespace"></a>
<a id="36-whitespace"></a>

<a id="whitespace"></a>
### 3.6 空白

标点周围的空格遵循标准排版规则。

圆括号、方括号或花括号内侧不留空白。

```python
Yes: spam(ham[1], {'eggs': 2}, [])
```

```python
No:  spam( ham[ 1 ], { 'eggs': 2 }, [ ] )
```

逗号、分号、冒号之前不留空白。逗号、分号、冒号之后要留空白，行尾除外。

```python
Yes: if x == 4:
         print(x, y)
     x, y = y, x
```

```python
No:  if x == 4 :
         print(x , y)
     x , y = y , x
```

开始参数列表、索引或切片的开括号之前不留空白。

```python
Yes: spam(1)
```

```python
No:  spam (1)
```

```python
Yes: dict['key'] = list[index]
```

```python
No:  dict ['key'] = list [index]
```

不要有行尾空白。

二元运算符两侧各留一个空格，适用于赋值（`=`）、比较（`==, <, >, !=, <>, <=, >=, in, not in, is, is not`）以及布尔运算（`and, or, not`）。算术运算符（`+`、`-`、`*`、`/`、`//`、`%`、`**`、`@`）周围的空格，就靠你自己判断了。

```python
Yes: x == 1
```

```python
No:  x<1
```

传关键字参数、或定义默认参数值时，`=` 两侧绝不留空格，只有一个例外：[有类型标注时](#typing-default-values)，默认参数值的 `=` *要*留空格。

```python
Yes: def complex(real, imag=0.0): return Magic(r=real, i=imag)
Yes: def complex(real, imag: float = 0.0): return Magic(r=real, i=imag)
```

```python
No:  def complex(real, imag = 0.0): return Magic(r = real, i = imag)
No:  def complex(real, imag: float=0.0): return Magic(r = real, i = imag)
```

不要用空格把连续几行里的记号纵向对齐，那会变成维护负担（适用于 `:`、`#`、`=` 等）：

```python
Yes:
  foo = 1000  # comment
  long_name = 2  # comment that should not be aligned

  dictionary = {
      'foo': 1,
      'long_name': 2,
  }
```

```python
No:
  foo       = 1000  # comment
  long_name = 2     # comment that should not be aligned

  dictionary = {
      'foo'      : 1,
      'long_name': 2,
  }
```


<a id="Python_Interpreter"></a>
<a id="s3.7-shebang-line"></a>
<a id="37-shebang-line"></a>

<a id="shebang-line"></a>
### 3.7 Shebang 行

多数 `.py` 文件不需要以 `#!` 行开头。按 [PEP-394](https://peps.python.org/pep-0394/)，程序的主文件用 `#!/usr/bin/env python3`（以支持 virtualenv）或 `#!/usr/bin/python3` 开头。

这一行供内核用来找到 Python 解释器，但 Python 在导入模块时会忽略它。只有打算直接执行的文件才需要它。

<a id="s3.8-comments-and-docstrings"></a>
<a id="s3.8-comments"></a>
<a id="38-comments-and-docstrings"></a>

<a id="documentation"></a>
### 3.8 注释与 docstring

务必对模块、函数、方法的 docstring 与行内注释使用正确的风格。

<a id="s3.8.1-comments-in-doc-strings"></a>
<a id="381-docstrings"></a>
<a id="comments-in-doc-strings"></a>

<a id="docstrings"></a>
#### 3.8.1 Docstring

Python 用 *docstring* 给代码写文档。docstring 是包、模块、类或函数里的第一条语句，即一个字符串。这些字符串可以通过对象的 `__doc__` 成员自动提取，`pydoc` 也会用到它们。（不妨对某个模块跑一下 `pydoc`，看看效果。）docstring 一律用三个双引号 `"""` 的格式（依据 [PEP 257](https://peps.python.org/pep-0257/)）。docstring 的组织方式是：先一行摘要（一句物理行，不超过 80 字符），以句号、问号或叹号结尾。若要写更多内容（鼓励这么做），摘要行之后必须空一行，然后其余内容从第一行第一个引号所在的列开始。docstring 还有更多格式准则，见下文。

<a id="s3.8.2-comments-in-modules"></a>
<a id="382-modules"></a>
<a id="comments-in-modules"></a>

<a id="module-docs"></a>
#### 3.8.2 模块

每个文件都应当包含许可证样板。按项目所用的许可证挑选合适的样板（例如 Apache 2.0、BSD、LGPL、GPL）。

文件应当以一段 docstring 开头，说明该模块的内容与用法。

```python
"""A one-line summary of the module or program, terminated by a period.

Leave one blank line.  The rest of this docstring should contain an
overall description of the module or program.  Optionally, it may also
contain a brief description of exported classes and functions and/or usage
examples.

Typical usage example:

  foo = ClassFoo()
  bar = foo.function_bar()
"""
```


<a id="s3.8.2.1-test-modules"></a>

<a id="test-docs"></a>
##### 3.8.2.1 测试模块

测试模块不要求模块级 docstring。`unittest.TestCase` 的子类不要求类 docstring，其 `test_` 方法也不要求方法 docstring。只重复类名或方法名的 docstring 应当省略。

docstring 应当在能提供额外信息时才写，例如说明测试应当怎么跑，或解释某种不寻常的搭建模式或外部依赖。

```python
"""This blaze test uses golden files.

You can update those files by running
`blaze run //foo/bar:foo_test -- --update_golden_files` from the `google3`
directory.
"""
```

提供不了新信息的 docstring 就不要写。

```python
"""Tests for foo.bar."""
```

或者

```python
  def test_foo_is_barred(self):
    """Tests that bar is applied to foo."""
```

<a id="s3.8.3-functions-and-methods"></a>
<a id="383-functions-and-methods"></a>
<a id="functions-and-methods"></a>

<a id="function-docs"></a>
#### 3.8.3 函数与方法

本节里，「函数」指方法、函数、生成器或属性。

凡是具备以下一条或多条性质的函数，docstring 都是必须的：

-   属于公开 API
-   规模不小
-   逻辑不直观

docstring 应当提供足够的信息，让人不必读函数代码就能写出对它的调用。docstring 应当描述函数的调用语法与语义，一般不包括实现细节，除非这些细节与函数怎么用有关。例如，某个函数作为副作用会改动它自己的某个参数，那就应当在 docstring 里点出来。除此之外，与调用者无关、但微妙而重要的实现细节，写成代码旁边的注释，比写进函数 docstring 更好。

docstring 可以是描述式（`"""Fetches rows from a Bigtable."""`），也可以是祈使式（`"""Fetch rows from a Bigtable."""`），但同一个文件里必须风格一致。`@property` 数据描述符的 docstring 应当与属性、<a href="#doc-function-args">函数参数</a>的 docstring 同风格（写 `"""The Bigtable path."""`，而不是 `"""Returns the Bigtable path."""`）。

函数的某些方面应当写在专门的小节里，列举如下。每节以一行标题开头，标题以冒号结尾。除标题外，各节内容都应当保持两格或四格的悬挂缩进（同一文件里保持一致）。若函数名与签名信息量足够、用一行 docstring 就能贴切描述，这些小节可以省略。

<a id="doc-function-args"></a>
[*Args:*](#doc-function-args)
:   按名字逐个列出参数。描述跟在名字之后，用冒号隔开，冒号后接一个空格或换行。若描述长到放不进单行 80 字符，就用比参数名多缩进两格或四格的悬挂缩进（与文件里其余 docstring 保持一致）。若代码里没有对应的类型标注，描述应当写明所需的类型。若函数接受 `*foo`（变长参数列表）和/或 `**bar`（任意关键字参数），它们应当写作 `*foo` 与 `**bar`。

<a id="doc-function-returns"></a>
[*Returns:*（生成器则用 *Yields:*）](#doc-function-returns)
:   描述返回值的语义，包括类型标注没能提供的类型信息。若函数只返回 None，本节不要求写。若 docstring 以 "Return"、"Returns"、"Yield" 或 "Yields" 开头（例如 `"""Returns row from Bigtable as a tuple of strings."""`），*且*开头那句已足以描述返回值，本节也可以省略。不要模仿旧式的「NumPy 风格」（[示例](https://numpy.org/doc/1.24/reference/generated/numpy.linalg.qr.html)），它常把一个元组返回值写得像多个各带名字的返回值（从不提元组）。改为这样描述这类返回值：「Returns: A tuple (mat_a, mat_b), where mat_a is ..., and ...」。docstring 里的这些辅助名字不必与函数体内用的任何名字对应（那些不属于 API）。若函数用了 `yield`（即是个生成器），`Yields:` 一节应当描述 `next()` 返回的对象，而不是调用所求得的那生成器对象本身。

<a id="doc-function-raises"></a>
[*Raises:*](#doc-function-raises)
:   列出与接口相关的所有异常，后面跟一段描述。异常名加冒号加空格或换行、以及悬挂缩进的风格，与 *Args:* 一节所述相同。若违背 docstring 里指定的 API 才导致某个异常被抛出，那就不该把它写进文档（否则就自相矛盾地把「违背 API 时的行为」也变成 API 的一部分了）。

```python
def fetch_smalltable_rows(
    table_handle: smalltable.Table,
    keys: Sequence[bytes | str],
    require_all_keys: bool = False,
) -> Mapping[bytes, tuple[str, ...]]:
    """Fetches rows from a Smalltable.

    Retrieves rows pertaining to the given keys from the Table instance
    represented by table_handle.  String keys will be UTF-8 encoded.

    Args:
        table_handle: An open smalltable.Table instance.
        keys: A sequence of strings representing the key of each table
          row to fetch.  String keys will be UTF-8 encoded.
        require_all_keys: If True only rows with values set for all keys will be
          returned.

    Returns:
        A dict mapping keys to the corresponding table row data
        fetched. Each row is represented as a tuple of strings. For
        example:

        {b'Serak': ('Rigel VII', 'Preparer'),
         b'Zim': ('Irk', 'Invader'),
         b'Lrrr': ('Omicron Persei 8', 'Emperor')}

        Returned keys are always bytes.  If a key from the keys argument is
        missing from the dictionary, then that row was not found in the
        table (and require_all_keys must have been False).

    Raises:
        IOError: An error occurred accessing the smalltable.
    """
```

同理，下面这种把 `Args:` 换行的变体也是允许的：

```python
def fetch_smalltable_rows(
    table_handle: smalltable.Table,
    keys: Sequence[bytes | str],
    require_all_keys: bool = False,
) -> Mapping[bytes, tuple[str, ...]]:
    """Fetches rows from a Smalltable.

    Retrieves rows pertaining to the given keys from the Table instance
    represented by table_handle.  String keys will be UTF-8 encoded.

    Args:
      table_handle:
        An open smalltable.Table instance.
      keys:
        A sequence of strings representing the key of each table row to
        fetch.  String keys will be UTF-8 encoded.
      require_all_keys:
        If True only rows with values set for all keys will be returned.

    Returns:
      A dict mapping keys to the corresponding table row data
      fetched. Each row is represented as a tuple of strings. For
      example:

      {b'Serak': ('Rigel VII', 'Preparer'),
       b'Zim': ('Irk', 'Invader'),
       b'Lrrr': ('Omicron Persei 8', 'Emperor')}

      Returned keys are always bytes.  If a key from the keys argument is
      missing from the dictionary, then that row was not found in the
      table (and require_all_keys must have been False).

    Raises:
      IOError: An error occurred accessing the smalltable.
    """
```

被装饰过的函数，其 docstring 必须描述装饰器作用到该函数之后的行为，因为那才是调用者会观察到的行为。

```python
@contextlib.contextmanager
def read_from_remote_filesystem(url: str) -> Iterator[RemoteFile]:
    """Open a file for reading from a remote filesystem.

    Args:
      url: The address of the remote file.

    Returns:
      A context manager providing a reference to a remote file.
    """
    reader = remote_file_reader(url)
    try:
        yield reader
    finally:
        reader.close()
```

<a id="s3.8.3.1-overridden-methods"></a>

<a id="overridden-method-docs"></a>
##### 3.8.3.1 被覆盖的方法

覆盖基类方法的方法，如果显式标了 [`@override`](https://typing.python.org/en/latest/spec/class-compat.html#override)，就不需要 docstring；除非覆盖方在行为上实质性地细化了基类的约定，或者需要提供某些细节（例如说明额外的副作用），这时覆盖方方法必须写一段至少涵盖这些差异的 docstring。

```python
from typing import override

class Parent:
  def do_something(self):
    """Parent method, includes docstring."""

# Child class, method annotated with override.
class Child(Parent):
  @override
  def do_something(self):
    pass
```

```python
# Child class, but without @override decorator, a docstring is required.
class Child(Parent):
  def do_something(self):
    pass

# Docstring is trivial, @override is sufficient to indicate that docs can be
# found in the base class.
class Child(Parent):
  @override
  def do_something(self):
    """See base class."""
```

<a id="s3.8.4-comments-in-classes"></a>
<a id="384-classes"></a>
<a id="comments-in-classes"></a>

<a id="class-docs"></a>
#### 3.8.4 类

类应当在类定义之下有一段描述该类的 docstring。公开属性（[属性（properties）](#properties)除外）应当在这里的 `Attributes` 一节里说明，格式与[函数的 `Args`](#doc-function-args) 一节相同。

```python
class SampleClass:
    """Summary of class here.

    Longer class information...
    Longer class information...

    Attributes:
        likes_spam: A boolean indicating if we like SPAM or not.
        eggs: An integer count of the eggs we have laid.
    """

    def __init__(self, likes_spam: bool = False):
        """Initializes the instance based on spam preference.

        Args:
          likes_spam: Defines if instance exhibits this preference.
        """
        self.likes_spam = likes_spam
        self.eggs = 0

    @property
    def butter_sticks(self) -> int:
        """The number of butter sticks we have."""
```

所有类 docstring 都应当以一行摘要开头，描述该类的实例代表什么。这意味着 `Exception` 的子类也应当描述该异常代表什么，而不是它可能在什么情境下发生。类 docstring 不应当重复没必要的信息，比如「这是个类」。

```python
# Yes:
class CheeseShopAddress:
  """The address of a cheese shop.

  ...
  """

class OutOfCheeseError(Exception):
  """No more cheese is available."""
```

```python
# No:
class CheeseShopAddress:
  """Class that describes the address of a cheese shop.

  ...
  """

class OutOfCheeseError(Exception):
  """Raised when no more cheese is available."""
```

若类被装饰过，其 docstring 必须描述装饰器作用到该类之后的行为。

<a id="s3.8.5-block-and-inline-comments"></a>
<a id="comments-in-block-and-inline"></a>
<a id="s3.8.5-comments-in-block-and-inline"></a>
<a id="385-block-and-inline-comments"></a>

<a id="comments"></a>
#### 3.8.5 块注释与行内注释

最后该写注释的地方是代码里棘手的部分。如果你觉得下一次[代码评审](http://en.wikipedia.org/wiki/Code_review)时得解释它，那现在就把它注释掉。复杂的操作在开始之前用几行注释说明。不直观的地方在行尾加注释。

```python
# We use a weighted dictionary search to find out where i is in
# the array.  We extrapolate position based on the largest num
# in the array and the array size and then do binary search to
# get the exact number.

if i & (i-1) == 0:  # True if i is 0 or a power of 2.
```

为提高可读性，这类注释应当与代码至少隔 2 个空格，以注释符 `#` 开头，`#` 之后到注释正文之间至少再留一个空格。

另一方面，绝不要描述代码本身。要假定读代码的人比你更懂 Python（尽管他不知道你想干什么）。

```python
# BAD COMMENT: Now go through the b array and make sure whenever i occurs
# the next element is i+1
```

<!-- The next section is copied from the C++ style guide. -->

<a id="s3.8.6-punctuation-spelling-and-grammar"></a>
<a id="386-punctuation-spelling-and-grammar"></a>
<a id="spelling"></a>
<a id="punctuation"></a>
<a id="grammar"></a>

<a id="punctuation-spelling-grammar"></a>
#### 3.8.6 标点、拼写与语法

注意标点、拼写与语法；写得好的注释读起来比写得差的省力。

注释应当像叙述性文字一样好读，大小写与标点都要正确。多数情况下，完整的句子比句子片段更好读。较短的注释，比如代码行尾的注释，有时可以不那么正式，但你自己的风格要一致。

虽然代码评审者指出你把该用分号的地方用成了逗号会让人有点烦，但让源代码保持高水准的清晰与可读性非常重要。正确的标点、拼写与语法有助于达成这一目标。

<a id="s3.10-strings"></a>
<a id="310-strings"></a>

<a id="strings"></a>
### 3.10 字符串

格式化字符串用 [f-string](https://docs.python.org/3/reference/lexical_analysis.html#f-strings)、`%` 运算符或 `format` 方法，即使参数全是字符串也一样。在几种字符串格式化方式之间怎么选，靠你自己拿主意。用 `+` 做一次拼接可以，但不要用 `+` 来格式化。

```python
Yes: x = f'name: {name}; score: {n}'
     x = '%s, %s!' % (imperative, expletive)
     x = '{}, {}'.format(first, second)
     x = 'name: %s; score: %d' % (name, n)
     x = 'name: %(name)s; score: %(score)d' % {'name':name, 'score':n}
     x = 'name: {}; score: {}'.format(name, n)
     x = a + b
```

```python
No: x = first + ', ' + second
    x = 'name: ' + name + '; score: ' + str(n)
```

避免在循环里用 `+` 与 `+=` 累加字符串。某些条件下，用加法累加字符串会导致运行时间变成平方级而非线性级。虽然 CPython 上这类常见累加可能被优化，但那属于实现细节；优化生效的条件不易预测，还可能变。改为把各个子串加进列表，循环结束后用 `''.join` 拼接该列表，或者把各子串写进 `io.StringIO` 缓冲区。这些手法稳定具有摊还线性的运行时间复杂度。

```python
Yes: items = ['<table>']
     for last_name, first_name in employee_list:
         items.append('<tr><td>%s, %s</td></tr>' % (last_name, first_name))
     items.append('</table>')
     employee_table = ''.join(items)
```

```python
No: employee_table = '<table>'
    for last_name, first_name in employee_list:
        employee_table += '<tr><td>%s, %s</td></tr>' % (last_name, first_name)
    employee_table += '</table>'
```

同一个文件里，字符串引号的选择要一致。选定 `'` 或 `"` 就别换了。为了免去在字符串里用反斜杠转义引号，换用另一种引号是可以的。

```python
Yes:
  Python('Why are you hiding your eyes?')
  Gollum("I'm scared of lint errors.")
  Narrator('"Good!" thought a happy Python reviewer.')
```

```python
No:
  Python("Why are you hiding your eyes?")
  Gollum('The lint. It burns. It burns us.')
  Gollum("Always the great lint. Watching. Watching.")
```

多行字符串优先用 `"""` 而不是 `'''`。只有当项目对普通字符串也用 `'` 时，才可以对所有非 docstring 的多行字符串用 `'''`。docstring 则无论如何都必须用 `"""`。

多行字符串不会随程序其余部分的缩进走。若需要避免在字符串里嵌入多余空格，就用拼接起来的单行字符串，或者用 [`textwrap.dedent()`](https://docs.python.org/3/library/textwrap.html#textwrap.dedent) 处理多行字符串，以去掉每行开头的空格：

```python
  No:
  long_string = """This is pretty ugly.
Don't do this.
"""
```

```python
  Yes:
  long_string = """This is fine if your use case can accept
      extraneous leading spaces."""
```

```python
  Yes:
  long_string = ("And this is fine if you cannot accept\n" +
                 "extraneous leading spaces.")
```

```python
  Yes:
  long_string = ("And this too is fine if you cannot accept\n"
                 "extraneous leading spaces.")
```

```python
  Yes:
  import textwrap

  long_string = textwrap.dedent("""\
      This is also fine, because textwrap.dedent()
      will collapse common leading spaces in each line.""")
```

注意这里用反斜杠不违反[禁止显式续行](#line-length)的规定；此处的反斜杠是在字符串字面量里[转义一个换行](https://docs.python.org/3/reference/lexical_analysis.html#string-and-bytes-literals)。

<a id="s3.10.1-logging"></a>
<a id="3101-logging"></a>
<a id="logging"></a>

<a id="logging"></a>
#### 3.10.1 日志

对于第一个参数期望模式字符串（带 % 占位符）的日志函数：一律用一个字符串字面量（不是 f-string！）作第一个参数，把模式参数作为后续参数传入。有些日志实现会把未展开的模式字符串收集为可供查询的字段。这样做还能避免花时间去渲染一条没有任何 logger 被配置为输出的消息。

```python
  Yes:
  import tensorflow as tf
  logger = tf.get_logger()
  logger.info('TensorFlow Version is: %s', tf.__version__)
```

```python
  Yes:
  import os
  from absl import logging

  logging.info('Current $PAGER is: %s', os.getenv('PAGER', default=''))

  homedir = os.getenv('HOME')
  if homedir is None or not os.access(homedir, os.W_OK):
    logging.error('Cannot write to home directory, $HOME=%r', homedir)
```

```python
  No:
  import os
  from absl import logging

  logging.info('Current $PAGER is:')
  logging.info(os.getenv('PAGER', default=''))

  homedir = os.getenv('HOME')
  if homedir is None or not os.access(homedir, os.W_OK):
    logging.error(f'Cannot write to home directory, $HOME={homedir!r}')
```

<a id="s3.10.2-error-messages"></a>
<a id="3102-error-messages"></a>
<a id="error-messages"></a>

<a id="error-messages"></a>
#### 3.10.2 错误消息

错误消息（例如 `ValueError` 这类异常上的消息字符串，或展示给使用者看的消息）应当遵循三条准则：

1.  消息必须与实际的错误状况精确相符。

2.  插入其中的片段必须总是能被清楚地识别为插入片段。

3.  应当便于简单的自动化处理（例如 grep）。

```python
  Yes:
  if not 0 <= p <= 1:
    raise ValueError(f'Not a probability: {p=}')

  try:
    os.rmdir(workdir)
  except OSError as error:
    logging.warning('Could not remove directory (reason: %r): %r',
                    error, workdir)
```

```python
  No:
  if p < 0 or p > 1:  # PROBLEM: also false for float('nan')!
    raise ValueError(f'Not a probability: {p=}')

  try:
    os.rmdir(workdir)
  except OSError:
    # PROBLEM: Message makes an assumption that might not be true:
    # Deletion might have failed for some other reason, misleading
    # whoever has to debug this.
    logging.warning('Directory already was deleted: %s', workdir)

  try:
    os.rmdir(workdir)
  except OSError:
    # PROBLEM: The message is harder to grep for than necessary, and
    # not universally non-confusing for all possible values of `workdir`.
    # Imagine someone calling a library function with such code
    # using a name such as workdir = 'deleted'. The warning would read:
    # "The deleted directory could not be deleted."
    logging.warning('The %s directory could not be deleted.', workdir)
```

<a id="s3.11-files-sockets-closeables"></a>
<a id="s3.11-files-and-sockets"></a>
<a id="311-files-and-sockets"></a>
<a id="files-and-sockets"></a>

<a id="files"></a>
### 3.11 文件、套接字与类似的有状态资源

用完文件与套接字后要显式关闭。这条规则自然延伸到内部使用套接字的可关闭资源（例如数据库连接），以及其他需要类似方式关闭的资源。仅举几例：还包括 [mmap](https://docs.python.org/3/library/mmap.html) 映射、[h5py File 对象](https://docs.h5py.org/en/stable/high/file.html)以及 [matplotlib.pyplot 图形窗口](https://matplotlib.org/2.1.0/api/_as_gen/matplotlib.pyplot.close.html)。

不必要地让文件、套接字或其他这类有状态对象保持打开，有许多坏处：

-   它们可能占用有限的系统资源，例如文件描述符。处理大量这类对象的代码，若用完之后不把它们及时归还系统，可能不必要地耗尽那些资源。
-   保持文件打开可能妨碍其他操作，例如移走或删除它们，或卸载文件系统。
-   程序里共享的文件与套接字，可能在其逻辑上已关闭之后仍被误读写。若它们确实被关闭了，试图读写就会抛异常，问题也就暴露得更早。

此外，尽管文件与套接字（以及一些行为类似的资源）会在对象被析构时自动关闭，把对象的生命周期与资源状态耦合在一起是糟糕的做法：

-   运行时何时真正调用 `__del__` 方法毫无保证。不同的 Python 实现采用不同的内存管理技术，例如延迟垃圾回收，这可能任意且无限期地延长对象的存活时间。
-   对该文件的意外引用，例如全局变量或异常回溯里的引用，可能让它比预期活得更久。

依赖终结器去做带可观察副作用的自动清理，几十年来、在多种语言里被反复重新发现会导致重大问题（例如 Java 方面见[这篇文章](https://wiki.sei.cmu.edu/confluence/display/java/MET12-J.+Do+not+use+finalizers)）。

管理文件与类似资源的首选方式是用 [`with` 语句](http://docs.python.org/reference/compound_stmts.html#the-with-statement)：

```python
with open("hello.txt") as hello_file:
    for line in hello_file:
        print(line)
```

对于不支持 `with` 语句的类文件对象，用 `contextlib.closing()`：

```python
import contextlib

with contextlib.closing(urllib.urlopen("http://www.python.org/")) as front_page:
    for line in front_page:
        print(line)
```

在基于上下文的资源管理确实不可行的罕见情况下，代码文档必须清楚地说明资源生命周期是如何管理的。

<a id="s3.12-todo-comments"></a>
<a id="312-todo-comments"></a>

<a id="todo"></a>
### 3.12 TODO 注释

对于临时的、短期的权宜方案，或者够用但不完美的代码，用 `TODO` 注释。

`TODO` 注释以全大写的 `TODO` 一词开头，后跟一个冒号，再跟一个包含上下文的资源链接，最好是 bug 引用。首选 bug 引用，因为 bug 是有跟踪的，也有后续评论。这段上下文之后，用一个连字符 `-` 引出一句说明。这样做是为了让 `TODO` 格式统一，好让人搜出更多的细节。

```python
# TODO: crbug.com/192795 - Investigate cpufreq optimizations.
```

旧式写法以前是推荐的，但在新代码里不鼓励使用：

```python
# TODO(crbug.com/192795): Investigate cpufreq optimizations.
# TODO(yourusername): Use a "\*" here for concatenation operator.
```

避免写那种把个人或团队当作上下文的 TODO：

```python
# TODO: @yourusername - File an issue and use a '*' for repetition.
```

若你的 `TODO` 属于「将来某天做某件事」这种形式，那就要么给出非常具体的日期（「Fix by November 2009」），要么给出非常具体的事件（「Remove this code when all clients can handle XML responses.」），好让后来的代码维护者能看懂。跟踪这类事情，用 issue 最合适。

<a id="s3.13-imports-formatting"></a>
<a id="313-imports-formatting"></a>

<a id="imports-formatting"></a>
### 3.13 import 的格式

import 应当各自独占一行；[`typing` 与 `collections.abc` 的导入是例外](#typing-imports)。

例如：

```python
Yes: from collections.abc import Mapping, Sequence
     import os
     import sys
     from typing import Any, NewType
```

```python
No:  import os, sys
```

import 一律放在文件顶部，紧跟在模块注释与 docstring 之后、模块全局变量与常量之前。import 应当按从最通用到最不通用来分组：

1.  Python future 导入语句。例如：

    ```python
    from __future__ import annotations
    ```

    关于这些语句的更多信息见[上文](#from-future-imports)。

2.  Python 标准库导入。例如：

    ```python
    import sys
    ```

3.  [第三方](https://pypi.org/)模块或包的导入。例如：

    
    ```python
    import tensorflow as tf
    ```

4.  代码仓库的子包导入。例如：

    
    ```python
    from otherproject.ai import mind
    ```

5.  **已弃用：** 与本文件同属一个顶层子包的应用专属导入。例如：

    
    ```python
    from myproject.backend.hgwells import time_machine
    ```

    你可能会看到更早的 Google Python 风格代码这么做，但现在不再要求了。**鼓励新代码不要再费这个事。** 把应用专属的子包导入与其他子包导入同等看待即可。

    
在每个分组内部，import 应当按各模块的完整包路径（即 `from path import ...` 里的 `path`）做字典序排序，忽略大小写。代码可以在各 import 分组之间放一个空行，也可以不放。

```python
import collections
import queue
import sys

from absl import app
from absl import flags
import bs4
import cryptography
import tensorflow as tf

from book.genres import scifi
from myproject.backend import huxley
from myproject.backend.hgwells import time_machine
from myproject.backend.state_machine import main_loop
from otherproject.ai import body
from otherproject.ai import mind
from otherproject.ai import soul

# Older style code may have these imports down here instead:
#from myproject.backend.hgwells import time_machine
#from myproject.backend.state_machine import main_loop
```


<a id="s3.14-statements"></a>
<a id="314-statements"></a>

<a id="statements"></a>
### 3.14 语句

一般一行只写一条语句。

不过，只有当整条语句放得进一行时，才可以把测试的结果与测试写在同一行。具体来说，`try`/`except` 绝不能这么写，因为 `try` 与 `except` 没法同时放进一行；`if` 则只有在没有 `else` 时才能这么写。

```python
Yes:

  if foo: bar(foo)
```

```python
No:

  if foo: bar(foo)
  else:   baz(foo)

  try:               bar(foo)
  except ValueError: baz(foo)

  try:
      bar(foo)
  except ValueError: baz(foo)
```

<a id="s3.15-accessors"></a>
<a id="s3.15-access-control"></a>
<a id="315-access-control"></a>
<a id="access-control"></a>
<a id="accessors"></a>

<a id="getters-and-setters"></a>
### 3.15 取值器与设值器

当取值与设值函数（也叫访问器与修改器）在获取或设置某个变量的值这件事上提供了有意义的作用或行为时，才用它。

具体来说，当获取或设置该变量很复杂、或代价显著时——无论是眼下还是可预见的将来——就应当使用它们。

例如，若一对取值器/设值器只是读写一个内部属性，那就该把这个内部属性直接公开。相比之下，若设置某个变量意味着让某些状态失效或重建，那就该用设值函数。函数调用本身就提示了正在发生一个可能不太简单的操作。另外，只在需要简单逻辑时，[属性（properties）](#properties)也是一个选项，或者重构到不再需要取值器与设值器。

取值器与设值器应当遵循[命名](#s3.16-naming)准则，例如 `get_foo()` 与 `set_foo()`。

如果过去的行为允许通过属性来访问，不要把新的取值器/设值器函数绑回该属性。仍在尝试用旧方式访问该变量的代码应当明显地崩掉，好让它们意识到复杂度的变化。

<a id="s3.16-naming"></a>
<a id="316-naming"></a>

<a id="naming"></a>
### 3.16 命名

`module_name`, `package_name`, `ClassName`, `method_name`, `ExceptionName`,
`function_name`, `GLOBAL_CONSTANT_NAME`, `global_var_name`, `instance_var_name`,
`function_parameter_name`, `local_var_name`, `query_proper_noun_for_thing`,
`send_acronym_via_https`.


名字应当有描述性。这包括函数、类、变量、属性、文件以及其他任何类型的具名实体。

避免缩写。尤其不要用对本项目之外的人有歧义或陌生的缩写，也不要靠删掉词中间的字母来缩写。

文件名一律用 `.py` 扩展名。绝不要用连字符。

<a id="s3.16.1-names-to-avoid"></a>
<a id="3161-names-to-avoid"></a>

<a id="names-to-avoid"></a>
#### 3.16.1 应当避免的名字

-   单字符名字，除非属于明确允许的情形：

    -   计数器或迭代器（例如 `i`、`j`、`k`、`v` 等）
    -   `try/except` 语句里作为异常标识符的 `e`。
    -   `with` 语句里作为文件句柄的 `f`
    -   无约束的私有[类型变量](#typing-type-var)（例如
        `_T = TypeVar("_T")`、`_P = ParamSpec("_P")`）
    -   与参考文献或算法里既有记号一致的名字（见[数学记号](#math-notation)）

    请注意不要滥用单字符命名。一般来说，描述性的程度应当与名字的可见范围成正比。例如，对 5 行的代码块来说，`i` 或许是个好名字，但在多层嵌套的作用域里，它很可能过于含糊。

-   任何包/模块名里的连字符（`-`）

-   `__双下划线开头并结尾__` 的名字（Python 保留）

-   冒犯性词汇

-   毫无必要地包含变量类型的名字（例如：`id_to_name_dict`）

<a id="s3.16.2-naming-conventions"></a>
<a id="3162-naming-convention"></a>

<a id="naming-conventions"></a>
#### 3.16.2 命名约定

-   总体遵循 [PEP 8 命名约定][PEP-8-naming-conventions]。

-   「内部」指模块内部，或类内部的受保护或私有部分。

-   加一个下划线（`_`）前缀对保护模块变量与函数有一定作用（linter 会对访问受保护成员报警告）。注意单元测试访问被测模块里的受保护常量是可以的。

-   给实例变量或方法加双下划线（`__`，即「dunder」）前缀，实际上会（通过名字改写）使该变量或方法对本类私有；我们不鼓励这么做，因为它影响可读性与可测试性，而且它并不*真的*私有。优先用单个下划线。

-   把相关的类与顶层函数放在同一个模块里。与 Java 不同，你没有必要限制自己每个模块只放一个类。

-   类名用 CapWords，模块名用 lower\_with\_under.py。虽然有些老模块叫 CapWords.py，但现在已经不鼓励这样，因为当模块恰好与某个类同名时会让人困惑。（「等等——我写的是 `import StringIO` 还是 `from StringIO import StringIO`？」）

    -   注意 [PEP 8 说][PEP-8-acronyms]：「在 CapWords 里使用缩写词时，把缩写词的每个字母都大写。因此 HTTPServerError 比 HttpServerError 好。」

-   新的*单元测试*文件遵循符合 PEP 8 的 lower\_with\_under 方法名，例如 `test_<method_under_test>_<state>`。为了与遵循 CapWords 函数名的遗留模块保持一致(\*)，以 `test` 开头的方法名里可以出现下划线，用来分隔名字的各个逻辑部分。一种可能的模式是 `test<MethodUnderTest>_<state>`。

[PEP-8-naming-conventions]: https://peps.python.org/pep-0008/#naming-conventions
[PEP-8-acronyms]: https://peps.python.org/pep-0008/#descriptive-naming-styles

<a id="naming-exemptions"></a>
##### 3.16.2.1 豁免

以下豁免适用于上述通用命名约定：

-   当 Python 代码与外部代码库或非 Python 代码库紧密耦合时，例如与 C++ 的外函数接口（FFI），为与另一方代码库保持一致，可以遵循不同的约定（[一致性](#consistency)）。

<a id="s3.16.3-file-naming"></a>
<a id="3163-file-naming"></a>

<a id="file-naming"></a>
#### 3.16.3 文件命名

Python 文件名必须有 `.py` 扩展名，且不得含连字符（`-`）。这样它们才能被导入并做单元测试。若希望某个可执行程序不带扩展名就能访问，用一个符号链接，或一个内容为 `exec "$0.py" "$@"` 的简单 bash 包装脚本。

<a id="s3.16.4-guidelines-derived-from-guidos-recommendations"></a>
<a id="3164-guidelines-derived-from-guidos-recommendations"></a>

<a id="guidelines-derived-from-guidos-recommendations"></a>
#### 3.16.4 源自 [Guido](https://en.wikipedia.org/wiki/Guido_van_Rossum) 建议的准则

<table rules="all" border="1" summary="Guidelines from Guido's Recommendations"
       cellspacing="2" cellpadding="2">

  <tr>
    <th>类型</th>
    <th>公开</th>
    <th>内部</th>
  </tr>

  <tr>
    <td>包</td>
    <td><code>lower_with_under</code></td>
    <td></td>
  </tr>

  <tr>
    <td>模块</td>
    <td><code>lower_with_under</code></td>
    <td><code>_lower_with_under</code></td>
  </tr>

  <tr>
    <td>类</td>
    <td><code>CapWords</code></td>
    <td><code>_CapWords</code></td>
  </tr>

  <tr>
    <td>异常</td>
    <td><code>CapWords</code></td>
    <td></td>
  </tr>

  <tr>
    <td>函数</td>
    <td><code>lower_with_under()</code></td>
    <td><code>_lower_with_under()</code></td>
  </tr>

  <tr>
    <td>全局/类常量</td>
    <td><code>CAPS_WITH_UNDER</code></td>
    <td><code>_CAPS_WITH_UNDER</code></td>
  </tr>

  <tr>
    <td>全局/类变量</td>
    <td><code>lower_with_under</code></td>
    <td><code>_lower_with_under</code></td>
  </tr>

  <tr>
    <td>实例变量</td>
    <td><code>lower_with_under</code></td>
    <td><code>_lower_with_under</code> (protected)</td>
  </tr>

  <tr>
    <td>方法名</td>
    <td><code>lower_with_under()</code></td>
    <td><code>_lower_with_under()</code> (protected)</td>
  </tr>

  <tr>
    <td>函数/方法参数</td>
    <td><code>lower_with_under</code></td>
    <td></td>
  </tr>

  <tr>
    <td>局部变量</td>
    <td><code>lower_with_under</code></td>
    <td></td>
  </tr>

</table>


<a id="s3.17-main"></a>
<a id="317-main"></a>

<a id="math-notation"></a>
#### 3.16.5 数学记号

对数学密集的代码，若短变量名与参考文献或算法里的既有记号一致，那么即使它们本来会违反本风格指南，也优先用短名。

使用基于既有记号的名字时：

1.  在注释或 docstring 里引用所有命名约定的来源，最好用指向学术资源本身的超链接。若来源无法访问，就把命名约定清楚地记录下来。
2.  公开 API 优先用符合 PEP 8 的 `descriptive_names`，因为它们在脱离上下文时被遇到的可能性大得多。
3.  用作用域很窄的 `pylint: disable=invalid-name` 指令来消除警告。变量很少时，对每个变量用行尾注释形式的指令；变量较多时，在块的开头使用该指令。

<a id="main"></a>
### 3.17 Main

在 Python 里，`pydoc` 以及单元测试都要求模块可导入。若某个文件打算当作可执行程序用，其主要功能应当放在 `main()` 函数里，并且你的代码在执行主程序之前应当总是检查 `if __name__ == '__main__'`，这样该模块被导入时主程序不会被执行。

用 [absl](https://github.com/abseil/abseil-py) 时，用 `app.run`：

```python
from absl import app
...

def main(argv: Sequence[str]):
    # process non-flag arguments
    ...

if __name__ == '__main__':
    app.run(main)
```

否则用：

```python
def main():
    ...

if __name__ == '__main__':
    main()
```

顶层的一切代码都会在模块被导入时执行。注意不要把「在文件被 `pydoc` 处理时不该执行」的函数调用、对象创建或其他操作放在那里。

<a id="s3.18-function-length"></a>
<a id="318-function-length"></a>

<a id="function-length"></a>
### 3.18 函数长度

优先写小而专注的函数。

我们承认长函数有时是合适的，所以对函数长度不设硬性上限。若某个函数超过大约 40 行，就想想能不能在不伤害程序结构的前提下把它拆开。

即便你的长函数现在工作得完美无缺，几个月后改它的人也可能加进新行为。这可能引出难以发现的 bug。让函数保持简短，别人读你的代码、改你的代码都会更容易。

你可能会在跟
一些
代码打交道时遇到又长又复杂的函数。不要畏惧修改既有代码：如果跟这样的函数打交道确实困难，你发现错误难以调试，或者你想在好几个不同的场景里复用它的一部分，那就考虑把这个函数拆成更小、更易管理的片段。

<a id="s3.19-type-annotations"></a>
<a id="319-type-annotations"></a>

<a id="type-annotations"></a>
### 3.19 类型标注

<a id="s3.19.1-general-rules"></a>
<a id="s3.19.1-general"></a>
<a id="3191-general-rules"></a>

<a id="typing-general"></a>
#### 3.19.1 通则

*   先熟悉一下[类型提示](https://docs.python.org/3/library/typing.html)。

*   一般不需要给 `self` 或 `cls` 加标注。如果为了得到正确的类型信息确实需要，可以用
    [`Self`](https://docs.python.org/3/library/typing.html#typing.Self)，例如

    ```python
    from typing import Self

    class BaseClass:
      @classmethod
      def create(cls) -> Self:
        ...

      def difference(self, other: Self) -> float:
        ...
    ```

*   同样，不必觉得非给 `__init__` 的返回值加标注不可（那里 `None` 是唯一合法选项）。

*   若有其他变量或返回值不应表达类型，用 `Any`。

*   不要求你给模块里所有函数都加标注。

    -   至少给公开 API 加标注。
    -   靠判断力在「安全与清晰」和「灵活」之间找到好的平衡。
    -   给容易出类型相关错误的代码加标注（以前出过 bug，或本身复杂）。
    -   给难以理解的代码加标注。
    -   等代码从类型角度看稳定下来之后再加标注。多数情况下，成熟代码里你可以给所有函数加标注，而不会损失太多灵活性。

<a id="s3.19.2-line-breaking"></a>
<a id="3192-line-breaking"></a>

<a id="typing-line-breaking"></a>
#### 3.19.2 折行

尽量遵循既有的[缩进](#indentation)规则。

加了标注之后，许多函数签名会变成「每行一个参数」。为确保返回类型也独占一行，可以在最后一个参数后面加一个逗号。

```python
def my_method(
    self,
    first_var: int,
    second_var: Foo,
    third_var: Bar | None,
) -> int:
  ...
```

始终优先在变量之间折行，而不要（例如）在变量名与类型标注之间折行。不过，如果全都放得进同一行，那就放一行。

```python
def my_method(self, first_var: int) -> int:
  ...
```

若函数名、最后一个参数与返回类型合起来太长，就换行缩进 4 格。需要折行时，优先让每个参数与返回类型各占一行，并把右括号与 `def` 对齐：

```python
Yes:
def my_method(
    self,
    other_arg: MyLongType | None,
) -> tuple[MyLongType1, MyLongType1]:
  ...
```

返回类型也可以选择与最后一个参数放在同一行：

```python
Okay:
def my_method(
    self,
    first_var: int,
    second_var: int) -> dict[OtherLongType, MyLongType]:
  ...
```

`pylint` 允许你把右括号移到新行并与开括号对齐，但这样可读性较差。

```python
No:
def my_method(self,
              other_arg: MyLongType | None,
             ) -> dict[OtherLongType, MyLongType]:
  ...
```

如上面的示例所示，尽量不拆分类型。但有时它们太长，放不进单行（尽量别把子类型拆开）。

```python
def my_method(
    self,
    first_var: tuple[list[MyLongType1],
                     list[MyLongType2]],
    second_var: list[dict[
        MyLongType3, MyLongType4]],
) -> None:
  ...
```

若单个名字加类型已经太长，考虑为该类型用一个[别名](#typing-aliases)。最后的办法是在冒号后折行并缩进 4 格。

```python
Yes:
def my_function(
    long_variable_name:
        long_module_name.LongTypeName,
) -> None:
  ...
```

```python
No:
def my_function(
    long_variable_name: long_module_name.
        LongTypeName,
) -> None:
  ...
```

<a id="s3.19.3-forward-declarations"></a>
<a id="3193-forward-declarations"></a>

<a id="forward-declarations"></a>
#### 3.19.3 前向声明

若你需要使用（同一模块里）尚未定义的类名——例如需要在该类的声明内部用到该类名，或者你用到的是代码后面才定义的类——那就用 `from __future__ import annotations`，或者用字符串表示类名。

```python
Yes:
from __future__ import annotations

class MyClass:
  def __init__(self, stack: Sequence[MyClass], item: OtherClass) -> None:

class OtherClass:
  ...
```

```python
Yes:
class MyClass:
  def __init__(self, stack: Sequence['MyClass'], item: 'OtherClass') -> None:

class OtherClass:
  ...
```

<a id="s3.19.4-default-values"></a>
<a id="3194-default-values"></a>

<a id="typing-default-values"></a>
#### 3.19.4 默认值

依据 [PEP-008](https://peps.python.org/pep-0008/#other-recommendations)，*只有*同时有类型标注与默认值的参数，才在 `=` 两侧留空格。

```python
Yes:
def func(a: int = 0) -> int:
  ...
```

```python
No:
def func(a:int=0) -> int:
  ...
```

<a id="s3.19.5-nonetype"></a>
<a id="s3.19.5-none-type"></a>
<a id="3195-nonetype"></a>

<a id="none-type"></a>
#### 3.19.5 NoneType

在 Python 类型系统里，`NoneType` 是「一等」类型；就类型标注而言，`None` 是 `NoneType` 的别名。若某个参数可以是 `None`，那就必须声明出来！你可以用 `|` 联合类型表达式（新的 Python 3.10+ 代码推荐这么做），也可以用较老的 `Optional` 与 `Union` 语法。

用显式的 `X | None`，不要用隐式的。较早版本的类型检查器允许把 `a: str = None` 解释为 `a: str | None = None`，但现在不再提倡这种行为。

```python
Yes:
def modern_or_union(a: str | int | None, b: str | None = None) -> str:
  ...
def union_optional(a: Union[str, int, None], b: Optional[str] = None) -> str:
  ...
```

```python
No:
def nullable_union(a: Union[None, str]) -> str:
  ...
def implicit_optional(a: str = None) -> str:
  ...
```

<a id="s3.19.6-type-aliases"></a>
<a id="s3.19.6-aliases"></a>
<a id="3196-type-aliases"></a>
<a id="typing-aliases"></a>

<a id="type-aliases"></a>
#### 3.19.6 类型别名

你可以为复杂类型声明别名。

*   别名应当用 CapWords 命名。若该别名只在本模块内使用，它就应当是 \_Private。
*   别名应当带上 `typing.TypeAlias` 类型标注，以便与非类型的全局变量区分开。
*   不要把类型别名当作 `from module import symbol` 这一惯用法的替代，那种写法是[不允许的](#imports)。

```python
Yes:
from typing import TypeAlias

_LossAndGradient: TypeAlias = tuple[tf.Tensor, tf.Tensor]
ComplexTFMap: TypeAlias = Mapping[str, _LossAndGradient]
```

```python
No:
import immutabledict

immutabledict = immutabledict.immutabledict
```

```python
# Acceptable for convenience (provided the alias is not used in a public API,
# such as a public function's signature, of this module).
_ShortName: TypeAlias = other_module.SignificantlyInconvenientlyLongName
```

<a id="s3.19.7-ignoring-types"></a>
<a id="s3.19.7-ignore"></a>
<a id="3197-ignoring-types"></a>

<a id="typing-ignore"></a>
#### 3.19.7 忽略类型

你可以用特殊注释 `# type:
ignore` 关掉某一行的类型检查。

`pytype` 有针对具体错误的关闭选项（与 lint 类似）：

```python
# pytype: disable=attribute-error
```

<a id="s3.19.8-typing-variables"></a>
<a id="s3.19.8-comments"></a>
<a id="3198-typing-internal-variables"></a>

<a id="typing-variables"></a>
#### 3.19.8 给变量标注类型

<a id="annotated-assignments"></a>
[*带标注的赋值*](#annotated-assignments)
:   若某个内部变量的类型难以或无法推断，就用带标注的赋值来指明它的类型——在变量名与值之间用冒号加类型（与有默认值的函数参数的写法相同）：

    ```python
    a: Foo = SomeUndecoratedFunction()
    ```

<a id="type-comments"></a>
[*类型注释*](#type-comments)
:   虽然你可能会看到代码库里还留着它们（在 Python 3.6 之前它们是必要的），不要再新增任何行尾 `# type: <类型名>`
    注释的用法：

    ```python
    a = SomeUndecoratedFunction()  # type: Foo
    ```

<a id="s3.19.9-tuples-vs-lists"></a>
<a id="s3.19.9-tuples"></a>
<a id="3199-tuples-vs-lists"></a>

<a id="typing-tuples"></a>
#### 3.19.9 元组与列表

带类型的列表只能装同一类型的对象。带类型的元组则要么是单一重复类型，要么是数量固定、类型各异的若干元素。后者常用作函数的返回类型。

```python
a: list[int] = [1, 2, 3]
b: tuple[int, ...] = (1, 2, 3)
c: tuple[int, str, float] = (1, "2", 3.5)
```

<a id="s3.19.10-typevars"></a>
<a id="s3.19.10-type-var"></a>
<a id="31910-typevar"></a>
<a id="typing-type-var"></a>

<a id="typevars"></a>
#### 3.19.10 类型变量

Python 类型系统有[泛型](https://docs.python.org/3/library/typing.html#generics)。类型变量（例如 `TypeVar` 与 `ParamSpec`）是使用泛型的一种常见方式。

例如：

```python
from collections.abc import Callable
from typing import ParamSpec, TypeVar
_P = ParamSpec("_P")
_T = TypeVar("_T")
...
def next(l: list[_T]) -> _T:
  return l.pop()

def print_when_called(f: Callable[_P, _T]) -> Callable[_P, _T]:
  def inner(*args: _P.args, **kwargs: _P.kwargs) -> _T:
    print("Function was called")
    return f(*args, **kwargs)
  return inner
```

`TypeVar` 可以加约束：

```python
AddableType = TypeVar("AddableType", int, float, str)
def add(a: AddableType, b: AddableType) -> AddableType:
  return a + b
```

`typing` 模块里一个常见的预定义类型变量是 `AnyStr`。当多处标注都可能是 `bytes` 或 `str`、且必须都是同一类型时，用它。

```python
from typing import AnyStr
def check_length(x: AnyStr) -> AnyStr:
  if len(x) <= 42:
    return x
  raise ValueError()
```

类型变量必须有描述性的名字，除非它同时满足以下全部条件：

*   对外不可见
*   无约束

```python
Yes:
  _T = TypeVar("_T")
  _P = ParamSpec("_P")
  AddableType = TypeVar("AddableType", int, float, str)
  AnyFunction = TypeVar("AnyFunction", bound=Callable)
```

```python
No:
  T = TypeVar("T")
  P = ParamSpec("P")
  _T = TypeVar("_T", int, float, str)
  _F = TypeVar("_F", bound=Callable)
```

<a id="s3.19.11-string-types"></a>
<a id="s3.19.11-strings"></a>
<a id="31911-string-types"></a>

<a id="typing-strings"></a>
#### 3.19.11 字符串类型

> 新代码不要用 `typing.Text`。它只为 Python 2/3 兼容而存在。

字符串/文本数据用 `str`。处理二进制数据的代码用 `bytes`。

```python
def deals_with_text_data(x: str) -> str:
  ...
def deals_with_binary_data(x: bytes) -> bytes:
  ...
```

若某个函数的所有字符串类型总是一致的，例如上面代码里返回类型与参数类型相同，就用 [AnyStr](#typing-type-var)。

<a id="s3.19.12-imports-for-typing"></a>
<a id="s3.19.12-imports"></a>
<a id="31912-imports-for-typing"></a>

<a id="typing-imports"></a>
#### 3.19.12 为类型标注而做的导入

对于来自 `typing` 或 `collections.abc` 模块、用于支撑静态分析与类型检查的符号（包括类型、函数与常量），一律直接导入符号本身。这样常见标注更简洁，也与世界各地的类型标注实践一致。明确允许你从 `typing` 与 `collections.abc` 模块在一行里导入多个具体符号。例如：

```python
from collections.abc import Mapping, Sequence
from typing import Any, Generic, cast, TYPE_CHECKING
```

鉴于这种导入方式会往本地命名空间里加条目，`typing` 或 `collections.abc` 里的名字应当当作关键字来对待，不要在你自己的 Python 代码里定义，无论是否加了类型。若某个类型与模块里已有的名字冲突，就用 `import x as y` 导入它。

```python
from typing import Any as AnyType
```

标注函数签名时，优先用 `collections.abc.Sequence` 这类抽象容器类型，而不是 `list` 这类具体类型。若确实需要用具体类型（例如由带类型元素构成的 `tuple`），优先用 `tuple` 这类内置类型，而不是 `typing` 模块里的参数化类型别名（例如 `typing.Tuple`）。

```python
from typing import List, Tuple

def transform_coordinates(original: List[Tuple[float, float]]) ->
    List[Tuple[float, float]]:
  ...
```

```python
from collections.abc import Sequence

def transform_coordinates(original: Sequence[tuple[float, float]]) ->
    Sequence[tuple[float, float]]:
  ...
```

<a id="s3.19.13-conditional-imports"></a>
<a id="31913-conditional-imports"></a>

<a id="typing-conditional-imports"></a>
#### 3.19.13 条件导入

只在例外情形下才用条件导入，即类型检查所需的额外导入在运行时必须避免时。这种模式不鼓励使用；应当优先考虑其他办法，例如重构代码以便使用顶层导入。

仅为类型标注所需的导入可以放在 `if
TYPE_CHECKING:` 块里。

-   条件导入的类型需要写成字符串引用，以便前向兼容 Python 3.6（那里标注表达式是真的会被求值的）。
-   这里只应当定义纯粹为类型标注所用的实体，包括别名。否则会变成运行时错误，因为该模块在运行时不会被导入。
-   该块应当紧跟在所有常规导入之后。
-   类型导入列表里不应有空行。
-   把这个列表当作常规导入列表来排序。
```python
import typing
if typing.TYPE_CHECKING:
  import sketch
def f(x: "sketch.Sketch"): ...
```

<a id="s3.19.14-circular-dependencies"></a>
<a id="s3.19.14-circular-deps"></a>
<a id="31914-circular-dependencies"></a>

<a id="typing-circular-deps"></a>
#### 3.19.14 循环依赖

因类型标注而导致的循环依赖是代码异味。这类代码是重构的好候选。虽然技术上可以保留循环依赖，但各种构建系统不会让你这么做，因为那样每个模块都得依赖另一个。

用 `Any` 替换掉造成循环依赖导入的模块。用一个有意义的名字设一个[别名](#typing-aliases)，并使用该模块里的真实类型名（`Any` 的任何属性都是 `Any`）。别名定义应当与最后一条导入之间空一行。

```python
from typing import Any

some_mod = Any  # some_mod.py imports this module.
...

def my_method(self, var: "some_mod.SomeType") -> None:
  ...
```

<a id="typing-generics"></a>
<a id="s3.19.15-generics"></a>
<a id="31915-generics"></a>

<a id="generics"></a>
#### 3.19.15 泛型

加标注时，优先在参数列表里为[泛型](https://docs.python.org/3/library/typing.html#generics)类型指明类型参数；否则泛型的参数会被假定为
[`Any`](https://docs.python.org/3/library/typing.html#the-any-type)。

```python
# Yes:
def get_names(employee_ids: Sequence[int]) -> Mapping[int, str]:
  ...
```

```python
# No:
# This is interpreted as get_names(employee_ids: Sequence[Any]) -> Mapping[Any, Any]
def get_names(employee_ids: Sequence) -> Mapping:
  ...
```

若某个泛型最合适的类型参数就是 `Any`，那就把它显式写出来，但要记住，很多情况下
[`TypeVar`](#typing-type-var) 可能更合适：

```python
# No:
def get_names(employee_ids: Sequence[Any]) -> Mapping[Any, str]:
  """Returns a mapping from employee ID to employee name for given IDs."""
```

```python
# Yes:
_T = TypeVar('_T')
def get_names(employee_ids: Sequence[_T]) -> Mapping[_T, str]:
  """Returns a mapping from employee ID to employee name for given IDs."""
```


<a id="4-parting-words"></a>

<a id="consistency"></a>
## 4 临别赠言

*保持一致*。

如果你在改代码，花几分钟看看周围的代码，判断一下它的风格。如果他们在索引变量名里用了 `_idx` 后缀，你也该用。如果他们的注释周围有一圈井号画成的小方框，你的注释也画上一圈小方框。

风格准则的意义在于拥有一套共同的编码词汇，这样人们能专注于你在说什么，而不是你说话的方式。我们在这里给出全局风格规则，好让大家知道这套词汇，但局部风格也很重要。若你加进某个文件的代码与周围既有代码看起来截然不同，读者读的时候就会被踢出节奏。

不过，一致性也有边界。它在局部、以及在全局风格未作规定的选择上作用更强。一般不该拿一致性当理由去沿用旧的风格，而不考虑新风格的好处，也不考虑代码库随时间向更新风格收敛的趋势。

