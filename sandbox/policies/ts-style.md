# Google TypeScript 风格指南

> 译自 Google 的 [TypeScript Style Guide](https://google.github.io/styleguide/tsguide.html)，
> 原文以 [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/) 许可发布。
> 本文件是其简体中文译本，供编码与审查时查阅：章节编号与层级照原文，
> 代码示例、标识符、类型标注与 JSDoc 标签一律保留英文原文。
>
> 原文的章节号由 CSS 计数器现生成，其中 **h5 一级本来就不带号**（渲染出来的页面即如此），
> 这里照原样保留，不是漏译。

本指南由 Google 内部使用的 TypeScript 风格指南删改而成，去掉了仅适用于 Google 内部的部分。Google 内部环境对 TypeScript 的约束与外部环境有所不同。这里给出的建议，对「打算把代码导入 Google」的人特别有用，在其他外部环境下则未必适用。

本版本没有自动部署流程，由志愿者按需推送。

## 1 引言

### 1.1 术语说明

本风格指南在使用*必须*（must）、*不得*（must not）、*应当*（should）、*不应*（should not）、*可以*（may）这些说法时，采用 [RFC 2119](https://tools.ietf.org/html/rfc2119) 的术语含义。*优先*（prefer）与*避免*（avoid）分别对应 *should* 与 *should not*。祈使句与陈述句是规定性的，对应 *must*。

### 1.2 指南说明

本文档中的示例代码**不具备规范效力**，只用于说明风格指南中的规范性表述。也就是说，示例虽然符合 Google 风格，但未必是表达该段代码的唯一合规范写法。示例中可选的格式选择不得作为规则强制执行。

## 2 源文件基础

### 2.1 文件编码：UTF-8

源文件一律使用 **UTF-8** 编码。

#### 2.1.1 空白字符

除行终止符之外，源文件中出现的唯一空白字符是 ASCII 水平空格（0x20）。这意味着字符串字面量中的其他空白字符一律要转义。

#### 2.1.2 特殊转义序列

凡是存在专用转义序列的字符（`\'`、`\"`、`\\`、`\b`、`\f`、`\n`、`\r`、`\t`、`\v`），一律使用该序列，不用对应的数字转义（如 `\x0a`、`\u000a` 或 `\u{a}`）。绝不使用遗留的八进制转义。

#### 2.1.3 非 ASCII 字符

其余非 ASCII 字符直接用 Unicode 字符本身（如 `∞`）。不可打印的字符可以使用等价的十六进制或 Unicode 转义（如 `\u221e`），并附一条说明性注释。

```
// Perfectly clear, even without a comment.
const units = 'μs';

// Use escapes for non-printable characters.
const output = '\ufeff' + content;  // byte order mark
```

```
// Hard to read and prone to mistakes, even with the comment.
const units = '\u03bcs'; // Greek letter mu, 's'

// The reader has no idea what this is.
const output = '\ufeff' + content;
```

## 3 源文件结构

文件由以下内容组成，**顺序固定**：

1. 版权信息，如有
2. 带 `@fileoverview` 的 JSDoc，如有
3. 导入，如有
4. 文件自身的实现

**存在的各段之间，恰好空一行。**

### 3.1 版权信息

文件里如果需要声明许可或版权信息，写在文件顶部的 JSDoc 里。

### 3.2 `@fileoverview` JSDoc

文件可以有一个顶层的 `@fileoverview` JSDoc。写了的话，可以用它说明文件内容、用途，或依赖信息。折行的部分不缩进。

例如：

```
/**
 * @fileoverview Description of file. Lorem ipsum dolor sit amet, consectetur
 * adipiscing elit, sed do eiusmod tempor incididunt.
 */
```

### 3.3 导入

ES6 与 TypeScript 的 import 语句有四种形态：

| 导入类型 | 示例 | 用途 |
| --- | --- | --- |
| module[module\_import] | `import * as foo from '...';` | TypeScript 导入 |
| named[destructuring\_import] | `import {SomeThing} from '...';` | TypeScript 导入 |
| default | `import SomeThing from '...';` | 仅用于其他需要它的外部代码 |
| side-effect | `import '...';` | 仅为在加载时引入库的副作用（如自定义元素） |

```
// Good: choose between two options as appropriate (see below).
import * as ng from '@angular/core';
import {Foo} from './foo';

// Only when needed: default imports.
import Button from 'Button';

// Sometimes needed to import libraries for their side effects:
import 'jasmine';
import '@polymer/paper-button';
```

#### 3.3.1 导入路径

TypeScript 代码*必须*用路径来导入其他 TypeScript 代码。路径*可以*是相对路径，即以 `.` 或 `..` 开头，也可以从根目录起算，如 `root/path/to/file`。

引用同一个（逻辑）项目内的文件时，代码*应当*用相对导入（`./foo`），而不是绝对导入 `path/to/foo` —— 这样整个项目搬到哪里都不用改这些导入。

留意限制 `../..` 的层数，`../../../` 这种写法会让模块与路径结构难以理解。

```
import {Symbol1} from 'path/from/root';
import {Symbol2} from '../parent/file';
import {Symbol3} from './sibling';
```

#### 3.3.2 命名空间导入与具名导入

命名空间导入与具名导入都可以用。

文件里频繁使用的符号、或者名字本身就清楚的符号，*优先*用具名导入，比如 Jasmine 的 `describe` 与 `it`。具名导入可以用 `as` 起一个更清楚的名字。

从庞大 API 里用到很多不同符号时，*优先*用命名空间导入。命名空间导入虽然带个 `*`，但与其他语言里的「通配」导入不是一回事：它给一个模块的全部导出起了一个名字，模块导出的每个符号都成为这个名字上的属性。对于那些名字很常见的导出符号（如 `Model`、`Controller`），命名空间导入不必声明别名就能提升可读性。

```
// Bad: overlong import statement of needlessly namespaced names.
import {Item as TableviewItem, Header as TableviewHeader, Row as TableviewRow,
  Model as TableviewModel, Renderer as TableviewRenderer} from './tableview';

let item: TableviewItem|undefined;
```

```
// Better: use the module for namespacing.
import * as tableview from './tableview';

let item: tableview.Item|undefined;
```

```
import * as testing from './testing';

// Bad: The module name does not improve readability.
testing.describe('foo', () => {
  testing.it('bar', () => {
    testing.expect(null).toBeNull();
    testing.expect(undefined).toBeUndefined();
  });
});
```

```
// Better: give local names for these common functions.
import {describe, it, expect} from './testing';

describe('foo', () => {
  it('bar', () => {
    expect(null).toBeNull();
    expect(undefined).toBeUndefined();
  });
});
```

##### 特例：Apps JSPB proto

Apps JSPB proto 必须使用具名导入，即使这样会让导入行变得很长。

这条规则是为了构建性能与死代码消除：`.proto` 文件里常常有很多 `message`，而且并不都要一起用。用具名（解构）导入，构建系统就能对 Apps JSPB message 建立更细粒度的依赖，同时又保留了按路径导入的便利。

```
// Good: import the exact set of symbols you need from the proto file.
import {Foo, Bar} from './foo.proto';

function copyFooBar(foo: Foo, bar: Bar) {...}
```

#### 3.3.3 重命名导入

代码*应当*用命名空间导入、或者直接重命名导出本身，来解决名字冲突。必要时，代码*可以*重命名导入（`import {SomeThing as SomeOtherThing}`）。

重命名有用的三种情形：

1. 需要避免与其他导入的符号冲突时。
2. 导入的符号名是生成出来的时候。
3. 导入的符号名字本身含义不清时，重命名能提升代码清晰度。例如用 RxJS 时，把 `from` 函数重命名为 `observableFrom` 可能更好读。

### 3.4 导出

所有代码都使用具名导出：

```
// Use named exports:
export class Foo { ... }
```

不要使用默认导出。这样才能保证所有导入都遵循同一种写法。

```
// Do not use default exports:
export default class Foo { ... } // BAD!
```

为什么？

默认导出没有固定名字，这让集中维护变得困难，而对代码所有者来说好处寥寥，还可能降低可读性：

```
import Foo from './bar';  // Legal.
import Bar from './bar';  // Also legal.
```

具名导出的好处是：导入语句试图导入一个没声明过的符号时会报错。在 `foo.ts` 里：

```
const foo = 'blah';
export default foo;
```

在 `bar.ts` 里：

```
import {fizz} from './foo';
```

会得到 `error TS2614: Module '"./foo"' has no exported member 'fizz'.`。而 `bar.ts` 里写：

```
import fizz from './foo';
```

结果是 `fizz === foo`，这大概出乎意料，而且很难排查。

此外，默认导出还会鼓励人们把什么都塞进一个大对象里一起命名空间化：

```
export default class Foo {
  static SOME_CONSTANT = ...
  static someHelpfulFunction() { ... }
  ...
}
```

用上面这种写法，我们有了文件作用域，可以拿它当命名空间；同时又多出一层或许并不需要的第二个作用域（类 `Foo`），它在别的文件里既可能被当作类型、也可能被当作值，含义暧昧。

所以，*优先*用文件作用域来划分命名空间，并使用具名导出：

```
export const SOME_CONSTANT = ...
export function someHelpfulFunction()
export class Foo {
  // only class stuff here
}
```

<a id="export-visibility"></a>
#### 3.4.1 导出可见性

TypeScript 不支持限制导出符号的可见性。只导出模块外部会用到的符号。总的来说，尽量缩小模块对外暴露的 API 面。

#### 3.4.2 可变的导出

不管语言层面支不支持，可变的导出都会让代码难以理解和调试，跨多个模块再导出（re-export）时尤其如此。这条风格要求换句话说是：**不允许 `export let`**。

```
export let foo = 3;
// In pure ES6, foo is mutable and importers will observe the value change after a second.
// In TS, if foo is re-exported by a second file, importers will not see the value change.
window.setTimeout(() => {
  foo = 4;
}, 1000 /* ms */);
```

如果需要支持外部可访问、且可变的绑定，*应当*改用显式的 getter 函数。

```
let foo = 3;
window.setTimeout(() => {
  foo = 4;
}, 1000 /* ms */);
// Use an explicit getter to access the mutable export.
export function getFoo() { return foo; };
```

对于「按条件导出两个值中的一个」这种常见写法，先做条件判断，再导出。要保证模块体执行完毕之后，所有导出都是定局。

```
function pickApi() {
  if (useOtherApi()) return OtherApi;
  return RegularApi;
}
export const SomeApi = pickApi();
```

#### 3.4.3 容器类

不要为了命名空间化的目的，创建只有静态方法或静态属性的容器类。

```
export class Container {
  static FOO = 1;
  static bar() { return 1; }
}
```

应当直接导出各个常量与函数：

```
export const FOO = 1;
export function bar() { return 1; }
```

### 3.5 导入与导出的类型标注

#### 3.5.1 导入类型

只在把导入的符号当作类型使用时，可以写 `import type {...}`。用作值时用普通导入：

```
import type {Foo} from './foo';
import {Bar} from './foo';

import {type Foo, Bar} from './foo';
```

为什么？

TypeScript 编译器会自动处理这个区别，不会为类型引用插入运行时的加载。那么为什么还要标出类型导入？

TypeScript 编译器有两种运行模式：

- 开发模式下，我们通常想要快速的迭代循环。编译器不做完整的类型信息检查，直接把代码转译成 JavaScript。这快得多，但在某些情况下要求写 `import type`。
- 生产模式下，我们要求正确性。编译器检查所有类型，并确保 `import type` 用得正确。

注意：如果需要为了副作用而强制产生一次运行时加载，用 `import '...';`。参见

#### 3.5.2 导出类型

再导出一个类型时，使用 `export type`，例如：

```
export type {AnInterface} from './foo';
```

为什么？

`export type` 的用处是让逐个文件转译（file-by-file transpilation）时也能再导出类型。参见
[`isolatedModules` 文档](https://www.typescriptlang.org/tsconfig#exports-of-non-value-identifiers)。

`export type` 看起来也能用来避免某个 API 导出值符号。但它同样给不出任何保证：下游代码仍然可能从别的路径导入这个 API。要把 API 的类型用法与值用法分开并加以保证，更好的办法是干脆把符号拆开，比如拆成 `UserService` 与 `AjaxUserService`。这既不容易出错，也更清楚地表达了意图。

#### 3.5.3 用模块，不用命名空间

TypeScript 支持两种组织代码的方式：*命名空间*（namespace）与*模块*（module），但命名空间是不允许用的。也就是说，代码*必须*用 `import {foo} from 'bar';` 这种形式的导入导出来引用其他文件里的代码。

代码*不得*使用 `namespace Foo { ... }` 结构。只有为了对接外部的第三方代码而必须如此时，*可以*用 `namespace`。要在语义上给代码划分命名空间，请拆成多个文件。

代码*不得*用 `require`（如 `import x = require('...');`）来导入。请使用 ES6 模块语法。

```
// Bad: do not use namespaces:
namespace Rocket {
  function launch() { ... }
}

// Bad: do not use <reference>
/// <reference path="..."/>

// Bad: do not use require()
import x = require('mydep');
```

> 附注：TypeScript 的 `namespace` 以前叫内部模块，用 `module Foo { ... }` 这种形式的 `module` 关键字。那个也别用。一律使用 ES6 导入。

## 4 语言特性

本节说明哪些特性可以用、哪些不可以用，以及对它们的使用还有什么额外约束。

本风格指南没有讨论到的语言特性*可以*使用，对其用法不作任何建议。

### 4.1 局部变量声明

#### 4.1.1 使用 const 与 let

声明变量一律用 `const` 或 `let`。默认用 `const`，除非变量需要重新赋值。绝不用 `var`。

```
const foo = otherValue;  // Use if "foo" never changes.
let bar = someValue;     // Use if "bar" is ever assigned into later on.
```

`const` 与 `let` 是块级作用域，与大多数其他语言里的变量一样。JavaScript 里的 `var` 是函数作用域，会引发难以理解的 bug。不要用它。

```
var foo = someValue;     // Don't use - var scoping is complex and causes bugs.
```

变量*不得*在声明之前使用。

#### 4.1.2 一个声明只声明一个变量

每个局部变量声明只声明一个变量：`let a = 1, b = 2;` 这种写法不用。

### 4.2 数组字面量

#### 4.2.1 不要使用 `Array` 构造函数

*不要*使用 `Array()` 构造函数，带不带 `new` 都不要。它的用法既混乱又自相矛盾：

```
const a = new Array(2); // [undefined, undefined]
const b = new Array(2, 3); // [2, 3];
```

应当一律用中括号写法来初始化数组，或用 `from` 初始化指定长度的 `Array`：

```
const a = [2];
const b = [2, 3];

// Equivalent to Array(2):
const c = [];
c.length = 2;

// [0, 0, 0, 0, 0]
Array.from<number>({length: 5}).fill(0);
```

#### 4.2.2 不要在数组上定义属性

不要在数组上定义或使用非数字属性（`length` 除外）。改用 `Map`（或 `Object`）。

#### 4.2.3 使用展开语法

展开语法 `[...foo];` 是浅拷贝或连接可迭代对象的一种便捷写法。

```
const foo = [
  1,
];

const foo2 = [
  ...foo,
  6,
  7,
];

const foo3 = [
  5,
  ...foo,
];

foo2[1] === 6;
foo3[1] === 1;
```

使用展开语法时，被展开的值*必须*与要创建的东西相匹配。创建数组时只能展开可迭代对象。原始值（包括 `null` 与 `undefined`）*不得*展开。

```
const foo = [7];
const bar = [5, ...(shouldUseFoo && foo)]; // might be undefined

// Creates {0: 'a', 1: 'b', 2: 'c'} but has no length
const fooStrings = ['a', 'b', 'c'];
const ids = {...fooStrings};
```

```
const foo = shouldUseFoo ? [7] : [];
const bar = [5, ...foo];
const fooStrings = ['a', 'b', 'c'];
const ids = [...fooStrings, 'd', 'e'];
```

#### 4.2.4 数组解构

数组字面量可以用在赋值左侧做解构（比如从单个数组或可迭代对象里拆出多个值）。可以带一个末尾的「剩余」元素（`...` 与变量名之间不留空格）。用不到的元素应当省略。

```
const [a, b, c, ...rest] = generateResults();
let [, b,, d] = someArray;
```

解构也可以用在函数参数上。数组解构参数如果是可选的，默认值一律写 `[]`，并在左侧给出各项的默认值：

```
function destructured([a = 4, b = 2] = []) { … }
```

不允许：

```
function badDestructuring([a, b] = [4, 2]) { … }
```

提示：往函数的参数或返回值里打包/拆包多个值时，尽量用对象解构而不是数组解构，这样既能给各个元素起名，也能各自指定类型。

### 4.3 对象字面量

#### 4.3.1 不要使用 `Object` 构造函数

`Object` 构造函数是不允许的。改用对象字面量（`{}` 或 `{a: 0, b: 1, c: 2}`）。

#### 4.3.2 遍历对象

用 `for (... in ...)` 遍历对象容易出错：它会把原型链上的可枚举属性一并包含进来。

不要使用不加过滤的 `for (... in ...)`：

```
for (const x in someObj) {
  // x could come from some parent prototype!
}
```

要么用 `if` 显式过滤值，要么用 `for (... of Object.keys(...))`。

```
for (const x in someObj) {
  if (!someObj.hasOwnProperty(x)) continue;
  // now x was definitely defined on someObj
}
for (const x of Object.keys(someObj)) { // note: for _of_!
  // now x was definitely defined on someObj
}
for (const [key, value] of Object.entries(someObj)) { // note: for _of_!
  // now key was definitely defined on someObj
}
```

#### 4.3.3 使用展开语法

展开语法 `{...bar}` 是创建对象浅拷贝的便捷写法。对象初始化时用展开语法，同一个键上后面的值会覆盖前面的值。

```
const foo = {
  num: 1,
};

const foo2 = {
  ...foo,
  num: 5,
};

const foo3 = {
  num: 5,
  ...foo,
}

foo2.num === 5;
foo3.num === 1;
```

使用展开语法时，被展开的值*必须*与要创建的东西相匹配。也就是说，创建对象时只能展开对象；数组与原始值（包括 `null` 与 `undefined`）*不得*展开。避免展开原型不是 Object 原型的对象（如类定义、类实例、函数）—— 这种行为不合直觉（只浅拷贝可枚举的非原型属性）。

```
const foo = {num: 7};
const bar = {num: 5, ...(shouldUseFoo && foo)}; // might be undefined

// Creates {0: 'a', 1: 'b', 2: 'c'} but has no length
const fooStrings = ['a', 'b', 'c'];
const ids = {...fooStrings};
```

```
const foo = shouldUseFoo ? {num: 7} : {};
const bar = {num: 5, ...foo};
```

#### 4.3.4 计算属性名

计算属性名（如 `{['key' + foo()]: 42}`）是允许的，并且算作字典式（带引号的）键（也就是说，不得与不带引号的键混用），除非这个计算属性是 [symbol](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Symbol)（如 `[Symbol.iterator]`）。

<a id="features-objects-destructuring"></a>
#### 4.3.5 对象解构

对象解构模式可以用在赋值左侧做解构，从单个对象里拆出多个值。

解构的对象也可以用作函数参数，但应尽量简单：只有一层、不带引号的简写属性。参数解构里不得使用更深层的嵌套与计算属性。默认值写在解构参数左侧（`{str = 'some default'} = {}`，而不是 `{str} = {str: 'some default'}`）；如果解构的对象本身是可选的，它必须默认为 `{}`。

例如：

```
interface Options {
  /** The number of times to do something. */
  num?: number;

  /** A string to do stuff to. */
  str?: string;
}

function destructured({num, str = 'default'}: Options = {}) {}
```

不允许：

```
function nestedTooDeeply({x: {num, str}}: {x: Options}) {}
function nontrivialDefault({num, str}: Options = {num: 42, str: 'default'}) {}
```

### 4.4 类

#### 4.4.1 类声明

类声明*不得*以分号结尾：

```
class Foo {
}
```

```
class Foo {
}; // Unnecessary semicolon
```

相对的，包含类表达式的语句*必须*以分号结尾：

```
export const Baz = class extends Bar {
  method(): number {
    return this.x;
  }
}; // Semicolon here as this is a statement, not a declaration
```

```
exports const Baz = class extends Bar {
  method(): number {
    return this.x;
  }
}
```

类声明的大括号与其他类内容之间是否用空行分隔，既不提倡也不禁止：

```
// No spaces around braces - fine.
class Baz {
  method(): number {
    return this.x;
  }
}

// A single space around both braces - also fine.
class Foo {

  method(): number {
    return this.x;
  }

}
```

#### 4.4.2 类方法声明

类方法声明*不得*用分号分隔各个方法声明：

```
class Foo {
  doThing() {
    console.log("A");
  }
}
```

```
class Foo {
  doThing() {
    console.log("A");
  }; // <-- unnecessary
}
```

方法声明应当与周围的代码用一个空行隔开：

```
class Foo {
  doThing() {
    console.log("A");
  }

  getOtherThing(): number {
    return 4;
  }
}
```

```
class Foo {
  doThing() {
    console.log("A");
  }
  getOtherThing(): number {
    return 4;
  }
}
```

##### 重写 toString

`toString` 方法可以重写，但它*必须*总能成功，且不得有可见的副作用。

提示：特别要当心在 toString 里调用其他方法 —— 异常情况可能导致无限循环。

#### 4.4.3 静态方法

##### 避免私有静态方法

在不影响可读性的前提下，*优先*用模块内的局部函数，而不是私有静态方法。

##### 不要依赖动态派发

代码*不应*依赖静态方法的动态派发。静态方法*应当*只在基类自身上调用（也就是直接定义它的那个类）。静态方法*不应*在持有动态实例的变量上调用 —— 该实例可能是构造函数，也可能是子类构造函数（若确实这样做，*必须*用 `@nocollapse` 标注）—— 也*不得*在自身没有定义该方法的子类上直接调用。

不允许：

```
// Context for the examples below (this class is okay by itself)
class Base {
  /** @nocollapse */ static foo() {}
}
class Sub extends Base {}

// Discouraged: don't call static methods dynamically
function callFoo(cls: typeof Base) {
  cls.foo();
}

// Disallowed: don't call static methods on subclasses that don't define it themselves
Sub.foo();

// Disallowed: don't access this in static methods.
class MyClass {
  static foo() {
    return this.staticField;
  }
}
MyClass.staticField = 1;
```

##### 避免静态 `this` 引用

代码*不得*在静态上下文中使用 `this`。

JavaScript 允许通过 `this` 访问静态字段。与其他语言不同，静态字段还是会被继承的。

```
class ShoeStore {
  static storage: Storage = ...;

  static isAvailable(s: Shoe) {
    // Bad: do not use `this` in a static method.
    return this.storage.has(s.id);
  }
}

class EmptyShoeStore extends ShoeStore {
  static storage: Storage = EMPTY_STORE;  // overrides storage from ShoeStore
}
```

为什么？

这段代码通常出乎意料：作者可能没料到静态字段能通过 this 指针访问，也可能没料到它们还能被重写 —— 这个特性并不常用。

这段代码还会鼓励「堆大量静态状态」这种坏味道，给可测试性带来问题。

#### 4.4.4 构造函数

构造函数的调用*必须*带圆括号，即使不传参数也一样：

```
const x = new Foo;
```

```
const x = new Foo();
```

省略圆括号可能导致不易察觉的错误。下面两行并不等价：

```
new Foo().Bar();
new Foo.Bar();
```

不必提供空的构造函数，也不必提供仅仅转发到父类的构造函数，因为不写构造函数时 ES2015 会提供默认的类构造函数。不过，带参数属性、可见性修饰符或参数装饰器的构造函数，即使函数体为空也*不应*省略。

```
class UnnecessaryConstructor {
  constructor() {}
}
```

```
class UnnecessaryConstructorOverride extends Base {
    constructor(value: number) {
      super(value);
    }
}
```

```
class DefaultConstructor {
}

class ParameterProperties {
  constructor(private myService) {}
}

class ParameterDecorators {
  constructor(@SideEffectDecorator myService) {}
}

class NoInstantiation {
  private constructor() {}
}
```

构造函数上下都应当与周围代码用一个空行隔开：

```
class Foo {
  myField = 10;

  constructor(private readonly ctorParam) {}

  doThing() {
    console.log(ctorParam.getThing() + myField);
  }
}
```

```
class Foo {
  myField = 10;
  constructor(private readonly ctorParam) {}
  doThing() {
    console.log(ctorParam.getThing() + myField);
  }
}
```

#### 4.4.5 类成员

##### 不用 #私有字段

不要使用私有字段（也叫私有标识符）：

```
class Clazz {
  #ident = 1;
}
```

应当改用 TypeScript 的可见性标注：

```
class Clazz {
  private ident = 1;
}
```

为什么？

私有标识符被 TypeScript 降级（down-level）时会带来明显的产物体积与性能退化，并且在 ES2015 之前不受支持。它们最多只能降级到 ES2015，不能再低。与此同时，既然有静态类型检查来约束可见性，它们也带不来什么实质好处。

##### 使用 readonly

在构造函数之外从不重新赋值的属性，用 `readonly` 修饰符标注（这些属性不必是深度不可变的）。

##### 参数属性

与其把显而易见的初始化过程一路接到类成员上，不如用 TypeScript 的[参数属性](https://www.typescriptlang.org/docs/handbook/2/classes.html#parameter-properties)。

```
class Foo {
  private readonly barService: BarService;

  constructor(barService: BarService) {
    this.barService = barService;
  }
}
```

```
class Foo {
  constructor(private readonly barService: BarService) {}
}
```

参数属性如果需要文档，[请使用 `@param` JSDoc 标签](#parameter-property-comments)。

##### 字段初始化

类成员如果不是参数，就在声明处初始化，有时这样还能把构造函数整个省掉。

```
class Foo {
  private readonly userList: string[];

  constructor() {
    this.userList = [];
  }
}
```

```
class Foo {
  private readonly userList: string[] = [];
}
```

提示：构造函数执行完毕后，绝不应给实例增删属性，这会严重妨碍虚拟机优化类的「形状」。以后才可能填上的可选字段，应当显式初始化为 `undefined`，以免之后形状发生变化。

<a id="properties-used-outside-of-class-lexical-scope"></a>
##### 在类的词法作用域之外使用的属性

在所属类的词法作用域之外使用的属性（例如 Angular 组件在模板里使用的属性），*不得*使用 `private` 可见性，因为它们用在了所属类词法作用域之外。

视属性的具体情况，使用 `protected` 或 `public`。Angular 与 AngularJS 的模板属性应当用 `protected`，而 Polymer 应当用 `public`。

TypeScript 代码*不得*用 `obj['foo']` 绕过属性的可见性。

为什么？

属性一旦标为 `private`，就是在向自动化系统和人类宣告：对该属性的访问限于声明它的类的方法之内，而他们会依赖这一点。例如，未使用代码检查会把看似未被使用的私有属性标出来，哪怕其他文件设法绕过了可见性限制。

虽然在 TypeScript 编译器看来 `obj['foo']` 似乎能绕过可见性，但调整构建规则就能让这种写法失效，而且它也违反了[优化兼容性](#optimization-compatibility)。

##### Getter 与 setter

类成员的 getter 与 setter（也叫访问器）*可以*使用。getter 方法*必须*是[纯函数](https://en.wikipedia.org/wiki/Pure_function)（即结果稳定、无副作用：getter *不得*改变可观察状态）。它们也适合用来限制内部实现细节或冗长实现细节的可见性（见下）。

```
class Foo {
  constructor(private readonly someService: SomeService) {}

  get someMember(): string {
    return this.someService.someVariable;
  }

  set someMember(newValue: string) {
    this.someService.someVariable = newValue;
  }
}
```

```
class Foo {
  nextId = 0;
  get next() {
    return this.nextId++; // Bad: getter changes observable state
  }
}
```

如果用访问器来隐藏某个类属性，被隐藏的属性*可以*加任意完整单词作为前缀或后缀，如 `internal` 或 `wrapped`。使用这些私有属性时，尽可能通过访问器取值。一个属性至少得有一个访问器不是直通式的：不要只为隐藏属性而定义「直通」访问器。应当把该属性设为 public（或者考虑把它设为 `readonly`，而不是只写一个没有 setter 的 getter）。

```
class Foo {
  private wrappedBar = '';
  get bar() {
    return this.wrappedBar || 'bar';
  }

  set bar(wrapped: string) {
    this.wrappedBar = wrapped.trim();
  }
}
```

```
class Bar {
  private barInternal = '';
  // Neither of these accessors have logic, so just make bar public.
  get bar() {
    return this.barInternal;
  }

  set bar(value: string) {
    this.barInternal = value;
  }
}
```

getter 与 setter *不得*用 `Object.defineProperty` 定义，因为这会干扰属性重命名。

##### 计算属性

在类里，只有当属性是 symbol 时才可以使用计算属性。字典式属性（即带引号或计算得出的非 symbol 键）不允许（见[不混用键类型的理由](#features-objects-mixing-keys)）。逻辑上可迭代的类，应当定义 `[Symbol.iterator]` 方法。除此之外，`Symbol` 应谨慎使用。

提示：当心使用其他内置 symbol（如 `Symbol.isConcatSpreadable`），编译器不为它们提供 polyfill，因此在较老的浏览器里不会生效。

#### 4.4.6 可见性

限制属性、方法与整个类型的可见性，有助于保持代码解耦。

- 尽可能限制符号的可见性。
- 考虑把私有方法改写成同一文件内、类之外的未导出函数，把私有属性挪到单独的未导出类里。
- TypeScript 符号默认是 public 的。除了在（构造函数里）声明非 readonly 的 public 参数属性之外，绝不要用 `public` 修饰符。

```
class Foo {
  public bar = new Bar();  // BAD: public modifier not needed

  constructor(public readonly baz: Baz) {}  // BAD: readonly implies it's a property which defaults to public
}
```

```
class Foo {
  bar = new Bar();  // GOOD: public modifier not needed

  constructor(public baz: Baz) {}  // public modifier allowed
}
```

另见[导出可见性](#export-visibility)。

#### 4.4.7 不允许的类写法

##### 不要直接操作 `prototype`

比起直接定义 `prototype` 属性，`class` 关键字能让类定义更清晰、更可读。普通实现代码没有理由去操作这些对象。混入（mixin）以及修改内置对象的原型，是明令禁止的。

**例外**：框架代码（如 Polymer 或 Angular）可能需要用到 `prototype`，不应为了避开它而采用更糟糕的变通写法。

### 4.5 函数

#### 4.5.1 术语

函数有很多种，彼此之间差别细微。本指南使用如下术语，与 [MDN](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Functions) 保持一致：

- 「函数声明」（function declaration）：使用 `function` 关键字的声明（即不是表达式）
- 「函数表达式」（function expression）：使用 `function` 关键字的表达式，通常用于赋值或作为参数传入
- 「箭头函数」（arrow function）：使用 `=>` 语法的表达式
- 「块体」（block body）：带大括号的箭头函数右侧
- 「简洁体」（concise body）：不带大括号的箭头函数右侧

方法与类/构造函数不在本节范围内。

#### 4.5.2 具名函数优先用函数声明

定义具名函数时，优先用函数声明，而不是箭头函数或函数表达式。

```
function foo() {
  return 42;
}
```

```
const foo = () => 42;
```

比如需要显式类型标注时，*可以*用箭头函数。

```
interface SearchFunction {
  (source: string, subString: string): boolean;
}

const fooSearch: SearchFunction = (source, subString) => { ... };
```

#### 4.5.3 嵌套函数

嵌套在其他方法或函数里的函数，*可以*视情况用函数声明或箭头函数。特别是在方法体内，优先用箭头函数，因为它们能访问外层的 `this`。

#### 4.5.4 不要使用函数表达式

不要使用函数表达式，改用箭头函数。

```
bar(() => { this.doSomething(); })
```

```
bar(function() { ... })
```

**例外**：*只有*当代码必须动态重新绑定 `this`（但这是[不提倡的](#rebinding-this)），或者用于生成器函数（生成器没有箭头语法）时，*才*可以使用函数表达式。

#### 4.5.5 箭头函数体

视情况使用简洁体（即表达式）或块体的箭头函数。

```
// Top level functions use function declarations.
function someFunction() {
  // Block bodies are fine:
  const receipts = books.map((b: Book) => {
    const receipt = payMoney(b.price);
    recordTransaction(receipt);
    return receipt;
  });

  // Concise bodies are fine, too, if the return value is used:
  const longThings = myValues.filter(v => v.length > 1000).map(v => String(v));

  function payMoney(amount: number) {
    // function declarations are fine, but must not access `this`.
  }

  // Nested arrow functions may be assigned to a const.
  const computeTax = (amount: number) => amount * 0.12;
}
```

只有当函数的返回值确实会用到时，才使用简洁体。用块体则能确保返回类型是 `void`，从而避免潜在的副作用。

```
// BAD: use a block body if the return value of the function is not used.
myPromise.then(v => console.log(v));
// BAD: this typechecks, but the return value still leaks.
let f: () => void;
f = () => 1;
```

```
// GOOD: return value is unused, use a block body.
myPromise.then(v => {
  console.log(v);
});
// GOOD: code may use blocks for readability.
const transformed = [1, 2, 3].map(v => {
  const intermediate = someComplicatedExpr(v);
  const more = acrossManyLines(intermediate);
  return worthWrapping(more);
});
// GOOD: explicit `void` ensures no leaked return value
myPromise.then(v => void console.log(v));
```

提示：`void` 运算符可以用来确保表达式体的箭头函数在结果不被使用时返回 `undefined`。

<a id="rebinding-this"></a>
#### 4.5.6 重新绑定 `this`

函数表达式与函数声明*不得*使用 `this`，除非它们的存在就是为了重新绑定 `this` 指针。多数情况下，重绑 `this` 都可以靠箭头函数或显式参数来避免。

```
function clickHandler() {
  // Bad: what's `this` in this context?
  this.textContent = 'Hello';
}
// Bad: the `this` pointer reference is implicitly set to document.body.
document.body.onclick = clickHandler;
```

```
// Good: explicitly reference the object from an arrow function.
document.body.onclick = () => { document.body.textContent = 'hello'; };
// Alternatively: take an explicit parameter
const setTextFn = (e: HTMLElement) => { e.textContent = 'hello'; };
document.body.onclick = setTextFn.bind(null, document.body);
```

绑定 `this` 时，优先用箭头函数，而不是 `f.bind(this)`、`goog.bind(f, this)` 或 `const self = this` 之类的做法。

#### 4.5.7 优先把箭头函数作为回调传入

回调被调用时可能收到意料之外的实参，这些实参能通过类型检查，却仍会导致逻辑错误。

避免把具名回调传给高阶函数，除非你确信两个函数的调用签名都是稳定的。特别要当心那些不常用的可选参数。

```
// BAD: Arguments are not explicitly passed, leading to unintended behavior
// when the optional `radix` argument gets the array indices 0, 1, and 2.
const numbers = ['11', '5', '10'].map(parseInt);
// > [11, NaN, 2];
```

应当改为传入一个箭头函数，显式地把参数转发给具名回调。

```
// GOOD: Arguments are explicitly passed to the callback
const numbers = ['11', '5', '3'].map((n) => parseInt(n));
// > [11, 5, 3]

// GOOD: Function is locally defined and is designed to be used as a callback
function dayFilter(element: string|null|undefined) {
  return element != null && element.endsWith('day');
}

const days = ['tuesday', undefined, 'juice', 'wednesday'].filter(dayFilter);
```

#### 4.5.8 箭头函数作为属性

类通常*不应*包含初始化为箭头函数的属性。箭头函数属性要求调用方知道被调者的 `this` 已经绑定好了，这加剧了对 `this` 究竟是什么的困惑，而且使用这类处理函数的调用点与引用看起来是坏的（即需要非局部的知识才能判断它们是对的）。代码*应当*总是用箭头函数来调用实例方法（`const handler = (x) => { this.listener(x); };`），*不应*获取或传递实例方法的引用（~~`const handler = this.listener; handler(x);`~~）。

> 注意：在某些特定情形下，比如在模板里绑定函数时，用箭头函数作为属性很有用，代码也更可读。对这条规则要自行判断。另见下面的 [`Event Handlers`](#event-handlers) 一节。

```
class DelayHandler {
  constructor() {
    // Problem: `this` is not preserved in the callback. `this` in the callback
    // will not be an instance of DelayHandler.
    setTimeout(this.patienceTracker, 5000);
  }
  private patienceTracker() {
    this.waitedPatiently = true;
  }
}
```

```
// Arrow functions usually should not be properties.
class DelayHandler {
  constructor() {
    // Bad: this code looks like it forgot to bind `this`.
    setTimeout(this.patienceTracker, 5000);
  }
  private patienceTracker = () => {
    this.waitedPatiently = true;
  }
}
```

```
// Explicitly manage `this` at call time.
class DelayHandler {
  constructor() {
    // Use anonymous functions if possible.
    setTimeout(() => {
      this.patienceTracker();
    }, 5000);
  }
  private patienceTracker() {
    this.waitedPatiently = true;
  }
}
```

<a id="event-handlers"></a>
#### 4.5.9 事件处理函数

不需要卸载处理函数时（例如事件由类自身发出），事件处理函数*可以*用箭头函数。如果处理函数需要卸载，正确做法就是把箭头函数作为属性，因为它们会自动捕获 `this`，并提供可供卸载的稳定引用。

```
// Event handlers may be anonymous functions or arrow function properties.
class Component {
  onAttached() {
    // The event is emitted by this class, no need to uninstall.
    this.addEventListener('click', () => {
      this.listener();
    });
    // this.listener is a stable reference, we can uninstall it later.
    window.addEventListener('onbeforeunload', this.listener);
  }
  onDetached() {
    // The event is emitted by window. If we don't uninstall, this.listener will
    // keep a reference to `this` because it's bound, causing a memory leak.
    window.removeEventListener('onbeforeunload', this.listener);
  }
  // An arrow function stored in a property is bound to `this` automatically.
  private listener = () => {
    confirm('Do you want to exit the page?');
  }
}
```

不要在安装事件处理函数的表达式里使用 `bind`，因为它会创建一个无法卸载的临时引用。

```
// Binding listeners creates a temporary reference that prevents uninstalling.
class Component {
  onAttached() {
    // This creates a temporary reference that we won't be able to uninstall
    window.addEventListener('onbeforeunload', this.listener.bind(this));
  }
  onDetached() {
    // This bind creates a different reference, so this line does nothing.
    window.removeEventListener('onbeforeunload', this.listener.bind(this));
  }
  private listener() {
    confirm('Do you want to exit the page?');
  }
}
```

#### 4.5.10 参数初始值

可选函数参数*可以*给一个默认初始值，在省略实参时使用。初始值*不得*有任何可观察的副作用。初始值*应当*尽可能简单。

```
function process(name: string, extraContext: string[] = []) {}
function activate(index = 0) {}
```

```
// BAD: side effect of incrementing the counter
let globalCounter = 0;
function newId(index = globalCounter++) {}

// BAD: exposes shared mutable state, which can introduce unintended coupling
// between function calls
class Foo {
  private readonly defaultPaths: string[];
  frobnicate(paths = defaultPaths) {}
}
```

默认参数要少用。当没有自然顺序的可选参数不止一小把时，*优先*用[解构](#features-objects-destructuring)来打造可读的 API。

#### 4.5.11 适当时优先用剩余参数与展开

用*剩余*参数（rest）而不是访问 `arguments`。绝不把局部变量或参数命名为 `arguments`，那会以令人困惑的方式遮蔽内置名字。

```
function variadic(array: string[], ...numbers: number[]) {}
```

用函数的展开语法，而不是 `Function.prototype.apply`。

#### 4.5.12 函数的格式

函数体开头或结尾不允许有空行。

函数体内*可以*少量使用单个空行，把语句分成*逻辑组*。

生成器应当把 `*` 贴在 `function` 与 `yield` 关键字上，写成 `function* foo()` 和 `yield* iter`，而不是 ~~`function *foo()`~~ 或 ~~`yield *iter`~~。

单参数箭头函数左侧的圆括号，推荐加，但不是必须。

剩余参数或展开语法里的 `...` 后面不要加空格。

```
function myFunction(...elements: number[]) {}
myFunction(...array, ...iterable, ...generator());
```

### 4.6 this

只在类构造函数与方法中、声明了显式 `this` 类型的函数中（如 `function func(this: ThisType, ...)`），或者定义在可以使用 `this` 的作用域里的箭头函数中，才使用 `this`。

绝不要用 `this` 指代全局对象、`eval` 的上下文、事件的目标，也不要给函数多余地 `call()` 或 `apply()`。

```
this.alert('Hello');
```

### 4.7 接口

### 4.8 原始类型字面量

#### 4.8.1 字符串字面量

##### 使用单引号

普通字符串字面量用单引号（`'`）界定，而不是双引号（`"`）。

提示：如果字符串里含有单引号字符，可以考虑用模板字符串，免得转义这个引号。

##### 不要续行

不要在普通字符串或模板字符串里使用*续行*（即用反斜杠在字符串字面量内部结束一行）。ES5 虽然允许，但斜杠后面若带上任何尾随空白，就会引出棘手的错误，而且对读者来说也更不明显。

不允许：

```
const LONG_STRING = 'This is a very very very very very very very long string. \
    It inadvertently contains long stretches of spaces due to how the \
    continued lines are indented.';
```

应当写成

```
const LONG_STRING = 'This is a very very very very very very long string. ' +
    'It does not contain long stretches of spaces because it uses ' +
    'concatenated strings.';
const SINGLE_STRING =
    'http://it.is.also/acceptable_to_use_a_single_long_string_when_breaking_would_hinder_search_discoverability';
```

##### 模板字符串

复杂的字符串拼接，*优先*用模板字符串（用 `` ` `` 界定），尤其是涉及多个字符串字面量时。模板字符串可以跨多行。

模板字符串跨多行时，不必遵循所在块的缩进，不过如果多出来的空白无关紧要，也可以遵循。

例如：

```
function arithmetic(a: number, b: number) {
  return `Here is a table of arithmetic operations:
${a} + ${b} = ${a + b}
${a} - ${b} = ${a - b}
${a} * ${b} = ${a * b}
${a} / ${b} = ${a / b}`;
}
```

#### 4.8.2 数字字面量

数字可以用十进制、十六进制、八进制或二进制书写。十六进制、八进制、二进制分别使用 `0x`、`0o`、`0b` 前缀，字母小写。除非紧跟着 `x`、`o` 或 `b`，否则绝不要带前导零。

<a id="type-coercion"></a>
#### 4.8.3 类型转换

TypeScript 代码*可以*用 `String()` 与 `Boolean()` 函数（注意：不带 `new`！）、字符串模板字面量或 `!!` 来做类型转换。

```
const bool = Boolean(false);
const str = String(aNumber);
const bool2 = !!str;
const str2 = `result: ${bool2}`;
```

枚举类型的值（包括枚举类型与其他类型的联合）*不得*用 `Boolean()` 或 `!!` 转成布尔值，必须改用比较运算符显式比较。

```
enum SupportLevel {
  NONE,
  BASIC,
  ADVANCED,
}

const level: SupportLevel = ...;
let enabled = Boolean(level);

const maybeLevel: SupportLevel|undefined = ...;
enabled = !!maybeLevel;
```

```
enum SupportLevel {
  NONE,
  BASIC,
  ADVANCED,
}

const level: SupportLevel = ...;
let enabled = level !== SupportLevel.NONE;

const maybeLevel: SupportLevel|undefined = ...;
enabled = level !== undefined && level !== SupportLevel.NONE;
```

为什么？

大多数情况下，枚举名在运行时映射到哪个数字或字符串值并不重要，因为源码里引用枚举类型的值时都按名字来。因此工程师习惯不去想这件事，于是「确实重要」的情形就很不讨喜，因为它会让人意外。把枚举转成布尔值正是这种情形：尤其是默认情况下，第一个声明的枚举值是假值（因为它是 0），其余都是真值，这大概出人意料。读代码的人看到用了某个枚举值，甚至可能不知道它是不是第一个声明的值。

不提倡用字符串拼接来转成字符串，因为我们会检查加号运算符的两个操作数类型是否匹配。

代码*必须*用 `Number()` 解析数值，并且*必须*显式检查其返回值是否为 `NaN`，除非从上下文看解析不可能失败。

注意：`Number('')`、`Number(' ')` 与 `Number('\t')` 会返回 `0` 而不是 `NaN`。`Number('Infinity')` 与 `Number('-Infinity')` 分别返回 `Infinity` 与 `-Infinity`。此外，`Number('1e+309')` 与 `Number('-1e+309')` 这类指数写法会溢出成 `Infinity`。这些情形可能需要特殊处理。

```
const aNumber = Number('123');
if (!isFinite(aNumber)) throw new Error(...);
```

代码*不得*用一元加号（`+`）把字符串转成数字。数字解析可能失败，有出人意料的边角情形，而且常常是坏味道（在错误的层做解析）。鉴于此，一元加号在代码评审中太容易被漏看。

```
const x = +y;
```

代码同样*不得*用 `parseInt` 或 `parseFloat` 解析数字，除非字符串不是十进制（见下）。这两个函数都会忽略字符串的尾随字符，从而可能掩盖错误情形（例如把 `12 dwarves` 解析成 `12`）。

```
const n = parseInt(someString, 10);  // Error prone,
const f = parseFloat(someString);    // regardless of passing a radix.
```

需要按进制解析的代码，在调用 `parseInt` 之前*必须*检查其输入只含该进制的合法数字；

```
if (!/^[a-fA-F0-9]+$/.test(someString)) throw new Error(...);
// Needed to parse hexadecimal.
// tslint:disable-next-line:ban
const n = parseInt(someString, 16);  // Only allowed for radix != 10
```

解析整数请用 `Number()` 再配合 `Math.floor` 或（可用时）`Math.trunc`：

```
let f = Number(someString);
if (isNaN(f)) handleError();
f = Math.floor(f);
```

##### 隐式转换

不要在本身就有隐式布尔转换的条件子句里再写显式的布尔转换。所谓条件子句，就是 `if`、`for` 与 `while` 语句里的条件。

```
const foo: MyInterface|null = ...;
if (!!foo) {...}
while (!!foo) {...}
```

```
const foo: MyInterface|null = ...;
if (foo) {...}
while (foo) {...}
```

[与显式转换一样](#type-coercion)，枚举类型的值（包括枚举类型与其他类型的联合）*不得*隐式转换成布尔值，必须改用比较运算符显式比较。

```
enum SupportLevel {
  NONE,
  BASIC,
  ADVANCED,
}

const level: SupportLevel = ...;
if (level) {...}

const maybeLevel: SupportLevel|undefined = ...;
if (level) {...}
```

```
enum SupportLevel {
  NONE,
  BASIC,
  ADVANCED,
}

const level: SupportLevel = ...;
if (level !== SupportLevel.NONE) {...}

const maybeLevel: SupportLevel|undefined = ...;
if (level !== undefined && level !== SupportLevel.NONE) {...}
```

其他类型的值，隐式转换成布尔值、或用比较运算符显式比较，都可以：

```
// Explicitly comparing > 0 is OK:
if (arr.length > 0) {...}
// so is relying on boolean coercion:
if (arr.length) {...}
```

### 4.9 控制结构

#### 4.9.1 控制流语句与块

控制流语句（`if`、`else`、`for`、`do`、`while` 等）一律用大括号包住代码，即使块体只有一条语句也一样。非空块的第一条语句必须另起一行。

```
for (let i = 0; i < x; i++) {
  doSomethingWith(i);
}

if (x) {
  doSomethingWithALongMethodNameThatForcesANewLine(x);
}
```

```
if (x)
  doSomethingWithALongMethodNameThatForcesANewLine(x);

for (let i = 0; i < x; i++) doSomethingWith(i);
```

**例外**：能写在一行里的 `if` 语句*可以*省略大括号。

```
if (x) x.doFoo();
```

##### 控制语句中的赋值

尽量避免在控制语句里给变量赋值。控制语句里的赋值很容易被误认为相等判断。

```
if (x = someFunction()) {
  // Assignment easily mistaken with equality check
  // ...
}
```

```
x = someFunction();
if (x) {
  // ...
}
```

如果确实倾向在控制语句里赋值，就给这个赋值外面再套一层圆括号，表明是有意为之。

```
while ((x = someFunction())) {
  // Double parenthesis shows assignment is intentional
  // ...
}
```

##### 遍历容器

遍历数组*优先*用 `for (... of someArr)`。`Array.prototype.forEach` 与朴素的 `for` 循环也是允许的：

```
for (const x of someArr) {
  // x is a value of someArr.
}

for (let i = 0; i < someArr.length; i++) {
  // Explicitly count if the index is needed, otherwise use the for/of form.
  const x = someArr[i];
  // ...
}
for (const [i, x] of someArr.entries()) {
  // Alternative version of the above.
}
```

`for`-`in` 循环只能用于字典式对象（更多信息见[下文](#optimization-compatibility-for-property-access)）。不要用 `for (... in ...)` 遍历数组，因为这会反直觉地给出数组的下标（还是字符串形式的！），而不是值：

```
for (const x in someArray) {
  // x is the index!
}
```

`for`-`in` 循环中应当用 `Object.prototype.hasOwnProperty` 排除不想要的、来自原型的属性。可能时，*优先*用 `for`-`of` 配合 `Object.keys`、`Object.values` 或 `Object.entries`，而不是 `for`-`in`。

```
for (const key in obj) {
  if (!obj.hasOwnProperty(key)) continue;
  doWork(key, obj[key]);
}
for (const key of Object.keys(obj)) {
  doWork(key, obj[key]);
}
for (const value of Object.values(obj)) {
  doWorkValOnly(value);
}
for (const [key, value] of Object.entries(obj)) {
  doWork(key, value);
}
```

#### 4.9.2 分组圆括号

只有当作者与评审者都同意「去掉圆括号不会让代码被合理解读成别的意思，而有它们也不会让代码更好读」时，才省略可选的分组圆括号。假设每个读者都背下了完整的运算符优先级表，是*不*合理的。

在 `delete`、`typeof`、`void`、`return`、`throw`、`case`、`in`、`of` 或 `yield` 之后，不要给整个表达式套多余的圆括号。

#### 4.9.3 异常处理

异常是这门语言的重要组成部分，出现异常情况时就应当使用。

自定义异常是从函数向外传递额外错误信息的好办法。只要原生 `Error` 类型不够用，就应当定义并使用它们。

优先抛异常，而不是临时拼凑的错误处理方式（比如传一个错误容器引用类型，或返回带 error 属性的对象）。

##### 用 `new` 实例化错误

实例化异常时一律用 `new Error()`，而不是直接调用 `Error()`。两种写法都会创建新的 `Error` 实例，但用 `new` 与其他对象的实例化方式更一致。

```
throw new Error('Foo is not a valid bar.');
```

```
throw Error('Foo is not a valid bar.');
```

##### 只抛错误对象

JavaScript（因而 TypeScript）允许抛出任意值，也允许用一个任意值 reject Promise。然而，抛出或 reject 的值如果不是 `Error`，就不会带上堆栈信息，使调试变得困难。这一处理方式同样适用于 `Promise` 的 reject 值，因为在 async 函数里 `Promise.reject(obj)` 等价于 `throw obj;`。

```
// bad: does not get a stack trace.
throw 'oh noes!';
// For promises
new Promise((resolve, reject) => void reject('oh noes!'));
Promise.reject();
Promise.reject('oh noes!');
```

应当只抛 `Error`（及其子类）：

```
// Throw only Errors
throw new Error('oh noes!');
// ... or subtypes of Error.
class MyError extends Error {}
throw new MyError('my oh noes!');
// For promises
new Promise((resolve) => resolve()); // No reject is OK.
new Promise((resolve, reject) => void reject(new Error('oh noes!')));
Promise.reject(new Error('oh noes!'));
```

##### 捕获与重新抛出

捕获错误时，代码*应当*假定所有抛出的错误都是 `Error` 的实例。

```
function assertIsError(e: unknown): asserts e is Error {
  if (!(e instanceof Error)) throw new Error("e is not an Error");
}

try {
  doSomething();
} catch (e: unknown) {
  // All thrown errors must be Error subtypes. Do not handle
  // other possible values unless you know they are thrown.
  assertIsError(e);
  displayError(e.message);
  // or rethrow:
  throw e;
}
```

异常处理器*不得*防御性地处理非 `Error` 类型，除非确知被调用的 API 会违反上述规则抛出非 `Error`。若确实如此，应当加一条注释，明确指出这些非 `Error` 从何而来。

```
try {
  badApiThrowingStrings();
} catch (e: unknown) {
  // Note: bad API throws strings instead of errors.
  if (typeof e === 'string') { ... }
}
```

为什么？

避免[过度防御性编程](https://en.wikipedia.org/wiki/Defensive_programming#Offensive_programming)。对大多数代码里根本不会存在的问题反复设防，只会产生没用的样板代码。

##### 空的 catch 块

捕获到异常后什么都不做，极少是正确的。当 catch 块里确实适合什么都不做时，要用注释说明这样做的理由。

```
  try {
    return handleNumericResponse(response);
  } catch (e: unknown) {
    // Response is not numeric. Continue to handle as text.
  }
  return handleTextResponse(response);
```

不允许：

```
  try {
    shouldFail();
    fail('expected an error');
  } catch (expected: unknown) {
  }
```

提示：与其他一些语言不同，上面这种写法根本行不通，因为 `fail` 抛出的错误同样会被捕获。请改用 `assertThrows()`。

#### 4.9.4 switch 语句

所有 `switch` 语句*必须*包含一个 `default` 语句组，即使其中没有代码。`default` 语句组必须放在最后。

```
switch (x) {
  case Y:
    doSomethingElse();
    break;
  default:
    // nothing to do.
}
```

在 switch 块内，每个语句组要么以 `break`、`return` 语句、要么以抛出异常的方式突然终止。非空的语句组（`case ...`）*不得*穿透（由编译器强制）：

```
switch (x) {
  case X:
    doSomething();
    // fall through - not allowed!
  case Y:
    // ...
}
```

空的语句组允许穿透：

```
switch (x) {
  case X:
  case Y:
    doSomething();
    break;
  default: // nothing to do.
}
```

#### 4.9.5 相等判断

一律用三等号（`===`）与不等号（`!==`）。双等号运算符会引发难以理解、且虚拟机执行更慢的类型转换。另见 [JavaScript 相等表](https://dorey.github.io/JavaScript-Equality-Table/)。

```
if (foo == 'bar' || baz != bam) {
  // Hard to understand behaviour due to type coercion.
}
```

```
if (foo === 'bar' || baz !== bam) {
  // All good here.
}
```

**例外**：与字面量 `null` 比较时*可以*使用 `==` 与 `!=`，以同时覆盖 `null` 与 `undefined`。

```
if (foo == null) {
  // Will trigger when foo is null or undefined.
}
```

#### 4.9.6 类型断言与非空断言

类型断言（`x as SomeType`）与非空断言（`y!`）都是不安全的。它们只是让 TypeScript 编译器闭嘴，并不会插入与这些断言相符的运行时检查，因此可能让你的程序在运行时崩溃。

因此，没有明显或明确理由时，*不应*使用类型断言与非空断言。

不要写下面这样：

```
(x as Foo).foo();

y!.bar();
```

要断言类型或非空性时，最好的答案是显式写一个执行该检查的运行时检查。

```
// assuming Foo is a class.
if (x instanceof Foo) {
  x.foo();
}

if (y) {
  y.bar();
}
```

有时由于代码的某些局部性质，你能确定断言写法是安全的。这种情况下，*应当*补充说明，解释为什么可以接受这种不安全的行为：

```
// x is a Foo, because ...
(x as Foo).foo();

// y cannot be null, because ...
y!.bar();
```

如果类型断言或非空断言背后的理由是显而易见的，那注释*可以*省略。例如，生成的 proto 代码永远是可空的，但在该代码的语境里，某些字段总是由后端提供，这可能已是众所周知。请自行判断。

##### 类型断言的语法

类型断言*必须*用 `as` 语法（而不是尖括号语法）。这能强制在访问成员时给断言加上圆括号。

```
const x = (<Foo>z).length;
const y = <Foo>z.length;
```

```
// z must be Foo because ...
const x = (z as Foo).length;
```

##### 双重断言

按 [TypeScript 手册](https://www.typescriptlang.org/docs/handbook/2/everyday-types.html#type-assertions)的说法，TypeScript 只允许转换成*更具体*或*更不具体*版本类型的类型断言。添加一个不符合该条件的类型断言（`x as Foo`）会报错："Conversion of type 'X' to type 'Y' may be a mistake because neither type sufficiently overlaps with the other."

如果你确定某个断言是安全的，可以做*双重断言*。这需要经由 `unknown` 来转换，因为它比所有类型都更不具体。

```
// x is a Foo here, because...
(x as unknown as Foo).fooMethod();
```

中间类型请用 `unknown`（而不是 `any` 或 `{}`）。

##### 类型断言与对象字面量

指定对象字面量的类型时，用类型标注（`: Foo`）而不是类型断言（`as Foo`）。这样当接口字段随时间变化时，能发现重构引入的 bug。

```
interface Foo {
  bar: number;
  baz?: string;  // was "bam", but later renamed to "baz".
}

const foo = {
  bar: 123,
  bam: 'abc',  // no error!
} as Foo;

function func() {
  return {
    bar: 123,
    bam: 'abc',  // no error!
  } as Foo;
}
```

```
interface Foo {
  bar: number;
  baz?: string;
}

const foo: Foo = {
  bar: 123,
  bam: 'abc',  // complains about "bam" not being defined on Foo.
};

function func(): Foo {
  return {
    bar: 123,
    bam: 'abc',   // complains about "bam" not being defined on Foo.
  };
}
```

#### 4.9.7 让 try 块保持聚焦

在不影响可读性的前提下，限制 try 块里的代码量。

```
try {
  const result = methodThatMayThrow();
  use(result);
} catch (error: unknown) {
  // ...
}
```

```
let result;
try {
  result = methodThatMayThrow();
} catch (error: unknown) {
  // ...
}
use(result);
```

把不会抛异常的语句挪出 try/catch 块，有助于读者看清哪个方法会抛异常。有些内联调用不抛异常，留在块里也无妨，因为它们可能不值得为临时变量多添一道麻烦。

**例外**：如果 try 块在循环里，可能会有性能问题。把 try 块放宽到覆盖整个循环是可以的。

### 4.10 装饰器

装饰器是带 `@` 前缀的语法，如 `@MyDecorator`。

不要定义新的装饰器。只使用框架定义的装饰器：

- Angular（如 `@Component`、`@NgModule` 等）
- Polymer（如 `@property`）

为什么？

我们总体上想避开装饰器，因为它们曾是一个实验性特性，此后已偏离 TC39 提案，并且带着一些已知的、不会再修的错误。

使用装饰器时，装饰器*必须*紧挨着它所修饰的符号，中间不能有空行：

```
/** JSDoc comments go before decorators */
@Component({...})  // Note: no empty line after the decorator.
class MyComp {
  @Input() myField: string;  // Decorators on fields may be on the same line...

  @Input()
  myOtherField: string;  // ... or wrap.
}
```

### 4.11 不允许的特性

#### 4.11.1 原始类型的包装对象

TypeScript 代码*不得*实例化原始类型 `String`、`Boolean`、`Number` 的包装类。包装类的行为出人意料，例如 `new Boolean(false)` 求值为 `true`。

```
const s = new String('hello');
const b = new Boolean(false);
const n = new Number(5);
```

包装类可以作为函数调用，用于类型转换（这比用 `+` 或与空字符串拼接更可取），或用于创建 symbol。更多信息见[类型转换](#type-coercion)。

#### 4.11.2 自动分号插入

不要依赖自动分号插入（ASI）。所有语句都显式用分号结尾。这能避免分号插入错误导致的 bug，也能确保与 ASI 支持有限的工具（如 clang-format）兼容。

#### 4.11.3 const 枚举

代码*不得*使用 `const enum`，改用普通 `enum`。

为什么？

TypeScript 枚举本来就不能被修改；`const enum` 是一个与优化相关的独立语言特性，它让枚举对模块的 JavaScript 使用者不可见。

#### 4.11.4 debugger 语句

生产代码中*不得*包含 debugger 语句。

```
function debugMe() {
  debugger;
}
```

#### 4.11.5 `with`

不要使用 `with` 关键字。它让代码更难理解，而且[自 ES5 起已在严格模式中被禁用](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Statements/with)。

#### 4.11.6 动态代码求值

不要使用 `eval` 或 `Function(...string)` 构造函数（代码加载器除外）。这些特性有潜在危险，而且在启用了严格[内容安全策略](https://developer.mozilla.org/en-US/docs/Web/HTTP/CSP)的环境里根本不能工作。

#### 4.11.7 非标准特性

不要使用非标准的 ECMAScript 或 Web 平台特性。

包括：

- 已被弃用、或已从 ECMAScript / Web 平台中完全移除的旧特性（见 [MDN](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Deprecated_and_obsolete_features)）
- 尚未标准化的新 ECMAScript 特性
  - 避免使用尚在 TC39 工作草案中、或正处在[提案流程](https://tc39.es/process-document/)中的特性
  - 只使用当前 ECMA-262 规范定义的 ECMAScript 特性
- 已提案但尚未完成的 Web 标准：
  - 未走完[提案流程](https://whatwg.org/faq#adding-new-features)的 WHATWG 提案
- 非标准的语言「扩展」（例如某些外部转译器提供的那些）

面向特定 JavaScript 运行时的项目，比如只面向最新 Chrome、Chrome 扩展、Node.JS、Electron，显然可以使用那些 API。考虑某个专有的、只由部分浏览器实现的 API 时请谨慎；想想有没有通用的库能替你把这层 API 抽象掉。

#### 4.11.8 修改内置对象

绝不要修改内置类型，无论是给它们的构造函数还是原型添加方法。避免依赖做这种事的库。

除非绝对必要（例如第三方 API 要求），不要给全局对象添加 symbol。

## 5 命名

### 5.1 标识符

标识符*必须*只使用 ASCII 字母、数字、下划线（用于常量与结构化的测试方法名），以及（很少用到的）'$' 符号。

<a id="naming-style"></a>
#### 5.1.1 命名风格

TypeScript 用类型来表达信息，因此名字*不应*带上是类型里已经包含的信息做修饰。（关于不该包含什么，另见 [Testing Blog](https://testing.googleblog.com/2017/10/code-health-identifiernamingpostforworl.html)。）

这条规则的一些具体例子：

- 私有属性或方法不要用前导或尾随下划线。
- 可选参数不要用 `opt_` 前缀。
  - 访问器请见下面的[访问器规则](#getters-and-setters-accessors)。
- 不要给接口加特殊标记（~~`IMyInterface`~~ 或 ~~`MyFooInterface`~~），除非所在环境的惯例如此。为类引入接口时，给它的名字要能表达这个接口当初为何存在（例如 `class TodoItem` 与 `interface TodoItemStorage`，如果该接口表达的是 JSON 存储/序列化所用的格式）。
- 给 `Observable` 加 `$` 后缀是常见的外部约定，有助于消除「可观察值 vs 具体值」的困惑。这个约定是否有用，交由各个团队自行判断，但在项目内*应当*保持一致。

#### 5.1.2 有描述性的名字

名字*必须*有描述性，对新读者来说清晰明白。不要使用含糊的、或对项目外读者不熟悉的缩写，也不要通过删掉单词中间的字母来缩写。

- **例外**：作用域不超过 10 行的变量（包括*不属于*导出 API 的参数）*可以*用短名字（如单个字母）。

```
// Good identifiers:
errorCount          // No abbreviation.
dnsConnectionIndex  // Most people know what "DNS" stands for.
referrerUrl         // Ditto for "URL".
customerId          // "Id" is both ubiquitous and unlikely to be misunderstood.
```

```
// Disallowed identifiers:
n                   // Meaningless.
nErr                // Ambiguous abbreviation.
nCompConns          // Ambiguous abbreviation.
wgcConnections      // Only your group knows what this stands for.
pcReader            // Lots of things can be abbreviated "pc".
cstmrId             // Deletes internal letters.
kSecondsPerDay      // Do not use Hungarian notation.
customerID          // Incorrect camelcase of "ID".
```

#### 5.1.3 驼峰

名字里的缩写要当作完整单词来对待，即写成 `loadHttpUrl` 而不是 ~~`loadHTTPURL`~~，除非平台名要求（如 `XMLHttpRequest`）。

#### 5.1.4 美元符号

标识符*不应*普遍使用 `$`，除非第三方框架的命名约定要求。[见上文](#naming-style)了解 `Observable` 值上的 `$` 用法。

### 5.2 按标识符类型分列的规则

大多数标识符名应当按下表的写法（依标识符类型而定）来定大小写。

| 风格 | 类别 |
| --- | --- |
| `UpperCamelCase` | 类 / 接口 / 类型 / 枚举 / 装饰器 / 类型参数 / TSX 里的组件函数 / JSXElement 类型参数 |
| `lowerCamelCase` | 变量 / 参数 / 函数 / 方法 / 属性 / 模块别名 |
| `CONSTANT_CASE` | 全局常量值，含枚举值。见下面的[常量](#identifiers-constants)。 |
| `#ident` | 私有标识符，从不使用。 |

#### 5.2.1 类型参数

类型参数（如 `Array<T>` 里的）*可以*用单个大写字母（`T`）或 `UpperCamelCase`。

#### 5.2.2 测试名

xUnit 风格测试框架里的测试方法名*可以*用 `_` 分隔，如 `testX_whenY_doesZ()`。

#### 5.2.3 `_` 前缀/后缀

标识符不得用 `_` 作前缀或后缀。

这也意味着 `_` *不得*单独作为标识符使用（例如用来表示某个参数未使用）。

> 提示：如果只需要数组（或 TypeScript 元组）里的部分元素，可以在解构语句里多插几个逗号，忽略中间的元素：
>
> ```
> const [a, , b] = [1, 5, 10];  // a <- 1, b <- 10
> ```

#### 5.2.4 导入

模块命名空间导入用 `lowerCamelCase`，而文件名用 `snake_case`，这意味着导入在大小写风格上确实会不一致，例如

```
import * as fooBar from './foo_bar';
```

有些库常用的命名空间导入前缀会违反这套命名规则，但开源界铺天盖地的用法让这种违规写法更可读。目前属于这一例外的库只有：

- [jquery](https://jquery.com/)，用 `$` 前缀
- [threejs](https://threejs.org/)，用 `THREE` 前缀

<a id="identifiers-constants"></a>
#### 5.2.5 常量

**不可变**：`CONSTANT_CASE` 表示一个值*有意*不被改动，也*可以*用于技术上可以被修改的值（即未被深度冻结的值），以向使用者表明不得修改。

```
const UNIT_SUFFIXES = {
  'milliseconds': 'ms',
  'seconds': 's',
};
// Even though per the rules of JavaScript UNIT_SUFFIXES is
// mutable, the uppercase shows users to not modify it.
```

常量也可以是类的 `static readonly` 属性。

```
class Foo {
  private static readonly MY_SPECIAL_NUMBER = 5;

  bar() {
    return 2 * Foo.MY_SPECIAL_NUMBER;
  }
}
```

**全局**：只有模块层声明的符号、模块层类的静态字段、以及模块层枚举的值，*可以*使用 `CONST_CASE`。如果一个值在程序生命周期内可以被实例化多次（例如函数内声明的局部变量，或嵌套在函数里的类上的静态字段），那它*必须*用 `lowerCamelCase`。

如果某个值是实现接口的箭头函数，那它*可以*声明为 `lowerCamelCase`。

#### 5.2.6 别名

为已有符号创建局部作用域的别名时，使用该标识符原有的格式。局部别名*必须*与来源的命名与格式一致。变量用 `const` 做局部别名，类字段用 `readonly` 属性。

> 注意：如果你创建别名只是为了在所选框架里把它暴露给模板，记得同样套上合适的[访问修饰符](#properties-used-outside-of-class-lexical-scope)。

```
const {BrewStateEnum} = SomeType;
const CAPACITY = 5;

class Teapot {
  readonly BrewStateEnum = BrewStateEnum;
  readonly CAPACITY = CAPACITY;
}
```

## 6 类型系统

### 6.1 类型推断

TypeScript 编译器对所有类型表达式（变量、字段、返回类型等）实现的类型推断，代码*可以*依赖。

```
const x = 15;  // Type inferred.
```

对能平凡推断出来的类型，省掉类型标注：初始化为 `string`、`number`、`boolean`、`RegExp` 字面量或 `new` 表达式的变量或参数。

```
const x: boolean = true;  // Bad: 'boolean' here does not aid readability
```

```
// Bad: 'Set' is trivially inferred from the initialization
const x: Set<string> = new Set();
```

为避免泛型类型参数被推断成 `unknown`，有时需要显式指定类型。例如用空值初始化泛型类型（如空数组、空对象、`Map` 或 `Set`）。

```
const x = new Set<string>();
```

对于更复杂的表达式，类型标注有助于提升程序的可读性：

```
// Hard to reason about the type of 'value' without an annotation.
const value = await rpc.getSomeValue().transform();
```

```
// Can tell the type of 'value' at a glance.
const value: string[] = await rpc.getSomeValue().transform();
```

是否需要标注由代码评审者决定。

#### 6.1.1 返回类型

函数与方法要不要写返回类型标注，由代码作者决定。评审者*可以*要求为难以理解的复杂返回类型加标注。项目*可以*有「总是要求写返回类型」的本地策略，但这不是 TypeScript 风格的普遍要求。

显式写出函数与方法的隐式返回值有两点好处：

- 更精确的文档，有利于代码读者。
- 日后若有改动函数返回类型的代码变更，能更快暴露潜在的类型错误。

### 6.2 undefined 与 null

TypeScript 支持 `undefined` 与 `null` 类型。可空类型可以写成联合类型（`string|null`）；`undefined` 同理。`undefined` 与 `null` 的联合没有特殊语法。

TypeScript 代码可以用 `undefined` 或 `null` 表示「没有值」，对此没有普遍性的取舍指引。许多 JavaScript API 用 `undefined`（如 `Map.get`），许多 DOM 与 Google API 用 `null`（如 `Element.getAttribute`），所以合适的空值取决于语境。

#### 6.2.1 可空/undefined 类型别名

类型别名*不得*在联合类型里包含 `|null` 或 `|undefined`。可空别名通常意味着 null 值在应用的太多层之间传来传去，而这会掩盖导致 `null` 的原始问题的来源。它们也让人看不清类或接口上某些值何时可能缺失。

代码*必须*只在真正使用该别名时*才*加上 `|null` 或 `|undefined`。代码*应当*在 null 值出现的近旁处理它们，使用上面提到的技术。

```
// Bad
type CoffeeResponse = Latte|Americano|undefined;

class CoffeeService {
  getLatte(): CoffeeResponse { ... };
}
```

```
// Better
type CoffeeResponse = Latte|Americano;

class CoffeeService {
  getLatte(): CoffeeResponse|undefined { ... };
}
```

#### 6.2.2 优先用可选而不是 `|undefined`

此外，TypeScript 支持用 `?` 表示可选参数与字段的特殊写法：

```
interface CoffeeOrder {
  sugarCubes: number;
  milk?: Whole|LowFat|HalfHalf;
}

function pourCoffee(volume?: Milliliter) { ... }
```

可选参数的类型隐式包含 `|undefined`。不过二者有所不同：构造值或调用方法时，可选参数可以被省略。例如 `{sugarCubes: 1}` 是合法的 `CoffeeOrder`，因为 `milk` 是可选的。

用可选字段（接口或类上的）与可选参数，而不是 `|undefined` 类型。

类则最好干脆避开这种写法，尽可能多地把字段初始化好。

```
class MyClass {
  field = '';
}
```

### 6.3 使用结构化类型

TypeScript 的类型系统是结构化的，而不是名义化的。也就是说，只要一个值至少具备该类型所要求的全部属性、且这些属性的类型（递归地）匹配，它就匹配这个类型。

提供基于结构的实现时，在符号的声明处显式写出类型（这样类型检查与报错更精确）。

```
const foo: Foo = {
  a: 123,
  b: 'abc',
}
```

```
const badFoo = {
  a: 123,
  b: 'abc',
}
```

用接口而不是类来定义结构化类型

```
interface Foo {
  a: number;
  b: string;
}

const foo: Foo = {
  a: 123,
  b: 'abc',
}
```

```
class Foo {
  readonly a: number;
  readonly b: number;
}

const foo: Foo = {
  a: 123,
  b: 'abc',
}
```

为什么？

上面那个 "badFoo" 对象依赖类型推断。可以给 "badFoo" 添加字段，而类型是根据对象本身推断出来的。

把 "badFoo" 传给接受 "Foo" 的函数时，报错会落在函数调用处，而不是对象声明处。在跨多个代码库修改接口表面时，这一点也很有用。

```
interface Animal {
  sound: string;
  name: string;
}

function makeSound(animal: Animal) {}

/**
 * 'cat' has an inferred type of '{sound: string}'
 */
const cat = {
  sound: 'meow',
};

/**
 * 'cat' does not meet the type contract required for the function, so the
 * TypeScript compiler errors here, which may be very far from where 'cat' is
 * defined.
 */
makeSound(cat);

/**
 * Horse has a structural type and the type error shows here rather than the
 * function call.  'horse' does not meet the type contract of 'Animal'.
 */
const horse: Animal = {
  sound: 'niegh',
};

const dog: Animal = {
  sound: 'bark',
  name: 'MrPickles',
};

makeSound(dog);
makeSound(horse);
```

### 6.4 优先用接口，而不是类型字面量别名

TypeScript 支持[类型别名](https://www.typescriptlang.org/docs/handbook/2/everyday-types.html#type-aliases)来给类型表达式命名。它可以用来给原始类型、联合、元组以及其他任何类型命名。

不过，为对象声明类型时，请用接口，而不是给对象字面量表达式起一个类型别名。

```
interface User {
  firstName: string;
  lastName: string;
}
```

```
type User = {
  firstName: string,
  lastName: string,
}
```

为什么？

这两种形式几乎等价，按照「两种形式里只选一种、以免出现写法差异」的原则，我们应当选定一种。此外，偏好接口还有[一些有意思的技术理由](https://ncjamieson.com/prefer-interfaces/)。该页面引用了 TypeScript 团队负责人的话："Honestly, my take is that it should really just be interfaces for anything that they can model. There is no benefit to type aliases when there are so many issues around display/perf."

### 6.5 `Array<T>` 类型

对简单类型（只含字母数字字符和点），用数组的语法糖 `T[]` 或 `readonly T[]`，而不是更长的 `Array<T>` 或 `ReadonlyArray<T>`。

对简单类型的多维非 `readonly` 数组，用语法糖形式（`T[][]`、`T[][][]`，依此类推），而不是更长的形式。

对更复杂的任何情况，用更长的形式 `Array<T>`。

这些规则在每一层嵌套上都适用，即嵌在更复杂类型里的简单 `T[]` 仍然写成 `T[]`，用语法糖。

```
let a: string[];
let b: readonly string[];
let c: ns.MyObj[];
let d: string[][];
let e: Array<{n: number, s: string}>;
let f: Array<string|number>;
let g: ReadonlyArray<string|number>;
let h: InjectionToken<string[]>;  // Use syntax sugar for nested types.
let i: ReadonlyArray<string[]>;
let j: Array<readonly string[]>;
```

```
let a: Array<string>;  // The syntax sugar is shorter.
let b: ReadonlyArray<string>;
let c: Array<ns.MyObj>;
let d: Array<string[]>;
let e: {n: number, s: string}[];  // The braces make it harder to read.
let f: (string|number)[];         // Likewise with parens.
let g: readonly (string | number)[];
let h: InjectionToken<Array<string>>;
let i: readonly string[][];
let j: (readonly string[])[];
```

### 6.6 可索引类型 / 索引签名（`{[key: string]: T}`）

在 JavaScript 里，把对象当作关联数组（也叫 "map"、"hash" 或 "dict"）用很常见。这类对象在 TypeScript 里可以用[索引签名](https://www.typescriptlang.org/docs/handbook/2/objects.html#index-signatures)（`[k: string]: T`）来标注类型：

```
const fileSizes: {[fileName: string]: number} = {};
fileSizes['readme.txt'] = 541;
```

在 TypeScript 里，给键起一个有意义的标签。（这个标签只起文档作用，其他方面并不使用。）

```
const users: {[key: string]: number} = ...;
```

```
const users: {[userName: string]: number} = ...;
```

> 与其用这些写法，不如考虑改用 ES6 的 `Map` 与 `Set` 类型。JavaScript 对象有[出人意料的不良行为](http://2ality.com/2012/01/objects-as-maps.html)，而 ES6 的类型能更明确地表达你的意图。另外，`Map` 可以用 `string` 以外的类型作键，`Set` 也可以包含 `string` 以外的类型。

TypeScript 内置的 `Record<Keys, ValueType>` 类型允许构造键集合确定的类型。它与关联数组的区别在于键是静态已知的。相关建议见[下文](#mapped-conditional-types)。

<a id="mapped-conditional-types"></a>
### 6.7 映射类型与条件类型

TypeScript 的[映射类型](https://www.typescriptlang.org/docs/handbook/2/mapped-types.html)与[条件类型](https://www.typescriptlang.org/docs/handbook/2/conditional-types.html)允许基于其他类型来指定新类型。TypeScript 标准库包含若干基于它们的类型运算符（`Record`、`Partial`、`Readonly` 等）。

这些类型系统特性可以简洁地指定类型，构造强大又类型安全的抽象。不过它们也带来若干缺点：

- 与显式指定属性及类型关系（例如用接口与继承，例子见下）相比，类型运算符要求读者在脑子里求值那个类型表达式。这可能让程序难读得多，与类型推断、以及跨文件边界的表达式结合时尤其如此。
- 映射类型与条件类型的求值模型 —— 特别是与类型推断结合时 —— 规定得不充分，不总被很好地理解，而且常常随 TypeScript 编译器版本变化。代码可能「碰巧」编译通过，或看起来给出了正确结果。这抬高了使用类型运算符的代码的长期维护成本。
- 映射类型与条件类型最强大的时候，是从复杂类型和/或推断出的类型派生类型。反过来说，这也正是它们最容易造出难懂、难维护的程序的时候。
- 有些语言工具不能很好地处理这些类型系统特性。例如 IDE 的查找引用（以及由此而来的重命名属性重构）找不到 `Pick<T, Keys>` 类型里的属性，Code Search 也不会给它们加超链接。

风格上的建议是：

- 始终使用能表达你的代码的最简单的类型构造。
- 一点点重复或啰嗦，往往比复杂类型表达式的长期成本便宜得多。
- 在上述考量之下，*可以*使用映射类型与条件类型。

例如，TypeScript 内置的 `Pick<T, Keys>` 类型允许通过取另一个类型 `T` 的子集来创建新类型，但简单的接口继承往往更容易理解。

```
interface User {
  shoeSize: number;
  favoriteIcecream: string;
  favoriteChocolate: string;
}

// FoodPreferences has favoriteIcecream and favoriteChocolate, but not shoeSize.
type FoodPreferences = Pick<User, 'favoriteIcecream'|'favoriteChocolate'>;
```

这等价于把属性逐个写出来：

```
interface FoodPreferences {
  favoriteIcecream: string;
  favoriteChocolate: string;
}
```

为减少重复，`User` 可以继承 `FoodPreferences`，或者（可能更好）嵌套一个食物偏好字段：

```
interface FoodPreferences { /* as above */ }
interface User extends FoodPreferences {
  shoeSize: number;
  // also includes the preferences.
}
```

在这里用接口让属性的分组显式可见，改善 IDE 支持，允许更好的优化，而且可以说让代码更容易理解。

<a id="any"></a>
### 6.8 `any` 类型

TypeScript 的 `any` 类型是所有其他类型的父类型与子类型，并且允许解引用任意属性。因此 `any` 是危险的 —— 它可能掩盖严重的编程错误，而且使用它会削弱「拥有静态类型」本身的价值。

**请考虑*不要*使用 `any`。** 在你想用 `any` 的情形下，考虑以下之一：

- [提供更具体的类型](#any-specific)
- [改用 `unknown`](#any-unknown)
- [抑制 lint 警告并记录原因](#any-suppress)

<a id="any-specific"></a>
#### 6.8.1 提供更具体的类型

使用接口、内联对象类型或类型别名：

```
// Use declared interfaces to represent server-side JSON.
declare interface MyUserJson {
  name: string;
  email: string;
}

// Use type aliases for types that are repetitive to write.
type MyType = number|string;

// Or use inline object types for complex returns.
function getTwoThings(): {something: number, other: string} {
  // ...
  return {something, other};
}

// Use a generic type, where otherwise a library would say `any` to represent
// they don't care what type the user is operating on (but note "Return type
// only generics" below).
function nicestElement<T>(items: T[]): T {
  // Find the nicest element in items.
  // Code can also put constraints on T, e.g. <T extends HTMLElement>.
}
```

<a id="any-unknown"></a>
#### 6.8.2 用 `unknown` 取代 `any`

`any` 类型允许赋值给任何其他类型，也允许在它上面解引用任意属性。这种特性往往既不必要也不可取，代码只是需要表达「这个类型未知」。这种情况下请用内置类型 `unknown` —— 它表达了这一概念，而且安全得多，因为它不允许解引用任意属性。

```
// Can assign any value (including null or undefined) into this but cannot
// use it without narrowing the type or casting.
const val: unknown = value;
```

```
const danger: any = value /* result of an arbitrary expression */;
danger.whoops();  // This access is completely unchecked!
```

要安全地使用 `unknown` 值，请用[类型守卫](https://www.typescriptlang.org/docs/handbook/advanced-types.html#type-guards-and-differentiating-types)缩窄类型

<a id="any-suppress"></a>
#### 6.8.3 抑制 `any` 的 lint 警告

有时候用 `any` 是合理的，比如在测试里构造 mock 对象。这种情况下，加一条抑制 lint 警告的注释，并把合理之处记录下来。

```
// This test only needs a partial implementation of BookService, and if
// we overlooked something the test will fail in an obvious way.
// This is an intentionally unsafe partial mock
// tslint:disable-next-line:no-any
const mockBookService = ({get() { return mockBook; }} as any) as BookService;
// Shopping cart is not used in this test
// tslint:disable-next-line:no-any
const component = new MyComponent(mockBookService, /* unused ShoppingCart */ null as any);
```

### 6.9 `{}` 类型

`{}` 类型也叫*空接口*类型，表示没有任何属性的接口。空接口类型没有指定任何属性，因此任何非空值都可以赋值给它。

```
let player: {};

player = {
  health: 50,
}; // Allowed.

console.log(player.health) // Property 'health' does not exist on type '{}'.
```

```
function takeAnything(obj:{}) {

}

takeAnything({});
takeAnything({ a: 1, b: 2 });
```

Google3 的代码在大多数场景下**不应**使用 `{}`。`{}` 表示任何非空的基本类型或对象类型，这很少合适。应优先用下面这几个更有描述性的类型之一：

- `unknown` 可以容纳任何值，包括 `null` 或 `undefined`，一般更适合不透明的值。
- `Record<string, T>` 更适合类字典对象，而且通过明确所含值（其本身也可以是 `unknown`）的类型 `T`，提供了更好的类型安全。
- `object` 同样排除基本类型，只留下非空的函数与对象，但对可能有哪些属性不作任何假设。

### 6.10 元组类型

如果你想创建一个 Pair 类型，请改用元组类型：

```
interface Pair {
  first: string;
  second: string;
}
function splitInHalf(input: string): Pair {
  ...
  return {first: x, second: y};
}
```

```
function splitInHalf(input: string): [string, string] {
  ...
  return [x, y];
}

// Use it like:
const [leftHalf, rightHalf] = splitInHalf('my string');
```

不过，给属性起有意义的名字往往更清楚。

如果声明一个 `interface` 太重，可以用内联对象字面量类型：

```
function splitHostPort(address: string): {host: string, port: number} {
  ...
}

// Use it like:
const address = splitHostPort(userAddress);
use(address.port);

// You can also get tuple-like behavior using destructuring:
const {host, port} = splitHostPort(userAddress);
```

### 6.11 包装类型

有几种与 JavaScript 原始类型相关的类型*不应*使用：

- `String`、`Boolean` 和 `Number` 的含义与对应的原始类型 `string`、`boolean`、`number` 略有不同。一律使用小写版本。
- `Object` 与 `{}` 和 `object` 都有相似之处，但稍微更宽松。如果某个类型应当包含除 `null` 与 `undefined` 之外的一切，请用 `{}`；要进一步排除其他原始类型（上面提到的三种，加上 `symbol` 与 `bigint`），请用小写的 `object`。

此外，绝不要把包装类型当作构造函数调用（带 `new`）。

### 6.12 只有返回类型的泛型

避免创建「泛型只出现在返回类型上」的 API。使用现成的这类 API 时，一律显式指定泛型。

## 7 工具链要求

Google 风格要求以特定方式使用若干工具，下面列出。

### 7.1 TypeScript 编译器

所有 TypeScript 文件都必须通过标准工具链的类型检查。

#### 7.1.1 @ts-ignore

不要使用 `@ts-ignore`，也不要使用它的变体 `@ts-expect-error` 或 `@ts-nocheck`。

为什么？

它们表面上像是「修掉」编译器错误的简单办法，但实际上，某个具体的编译器错误往往源于一个更大的问题，而那个问题可以更直接地修掉。

例如，如果你用 `@ts-ignore` 压掉一个类型错误，就很难预料周围的代码最终会看到什么类型。对许多类型错误来说，[如何最好地使用 `any`](#any) 里的建议是有用的。

在单元测试里*可以*使用 `@ts-expect-error`，不过一般*不应*用。`@ts-expect-error` 会压掉所有错误，很容易一不小心匹配过头，把更严重的错误也一并压掉。考虑下面之一：

- 测试那些需要在运行时处理未检查值的 API 时，给期望的类型或 `any` 加类型转换，并加一条解释性注释。这把错误抑制限制在单个表达式上。
- 抑制 lint 警告并记录原因，类似于[抑制 `any` 的 lint 警告](#any-suppress)。

### 7.2 符合性

Google TypeScript 包含若干*符合性框架*：[tsetse](https://tsetse.info) 与 [tsec](https://github.com/google/tsec)。

这些规则常用来强制关键限制（例如定义全局符号，这可能弄坏整个代码库）与安全模式（例如使用 `eval`，或给 `innerHTML` 赋值），或者更宽松地用来提升代码质量。

Google 风格的 TypeScript 必须遵守所有适用的全局或框架局部符合性规则。

## 8 注释与文档

#### 8.0.1 JSDoc 与普通注释

注释有两类：JSDoc（`/** ... */`）与非 JSDoc 的普通注释（`// ...` 或 `/* ... */`）。

- 用 `/** JSDoc */` 注释写文档，即代码的使用者应当阅读的注释。
- 用 `// 行注释` 写实现注释，即只关乎代码自身实现的注释。

JSDoc 注释能被工具理解（例如编辑器和文档生成器），而普通注释只面向其他人。

#### 8.0.2 多行注释

多行注释与周围代码缩进层级相同。它们*必须*使用多条单行注释（`//` 风格），而不是块注释风格（`/* */`）。

```
// This is
// fine
```

```
/*
 * This should
 * use multiple
 * single-line comments
 */

/* This should use // */
```

注释不要用星号或其他字符画成方框。

### 8.1 JSDoc 的一般形式

JSDoc 注释的基本格式如本例所示：

```
/**
 * Multiple lines of JSDoc text are written here,
 * wrapped normally.
 * @param arg A number to do something to.
 */
function doSomething(arg: number) { … }
```

或者单行例子：

```
/** This short jsdoc describes the function. */
function doSomething(arg: number) { … }
```

单行注释如果溢出成多行，*必须*改用 `/**` 与 `*/` 各占一行的多行风格。

许多工具从 JSDoc 注释里提取元数据来做代码校验与优化。因此这些注释*必须*格式良好。

### 8.2 Markdown

JSDoc 用 Markdown 书写，必要时*可以*包含 HTML。

这意味着解析 JSDoc 的工具会忽略纯文本的排版，所以如果你这样写：

```
/**
 * Computes weight based on three factors:
 *   items sent
 *   items received
 *   last timestamp
 */
```

渲染出来会是这样：

```
Computes weight based on three factors: items sent items received last timestamp
```

应当改写成 Markdown 列表：

```
/**
 * Computes weight based on three factors:
 *
 * - items sent
 * - items received
 * - last timestamp
 */
```

### 8.3 JSDoc 标签

Google 风格允许 JSDoc 标签的一个子集。大多数标签必须独占一行，标签位于行首。

```
/**
 * The "param" tag must occupy its own line and may not be combined.
 * @param left A description of the left param.
 * @param right A description of the right param.
 */
function add(left: number, right: number) { ... }
```

```
/**
 * The "param" tag must occupy its own line and may not be combined.
 * @param left @param right
 */
function add(left: number, right: number) { ... }
```

### 8.4 折行

折行的块级标签缩进四格。折行的描述文本*可以*与前面几行的描述对齐，但不提倡这种水平对齐。

```
/**
 * Illustrates line wrapping for long param/return descriptions.
 * @param foo This is a param with a particularly long description that just
 *     doesn't fit on one line.
 * @return This returns something that has a lengthy description too long to fit
 *     in one line.
 */
exports.method = function(foo) {
  return 5;
};
```

折行 `@desc` 或 `@fileoverview` 的描述时不缩进。

### 8.5 为模块的所有顶层导出写文档

用 `/** JSDoc */` 注释向代码使用者传达信息。避免仅仅复述属性名或参数名。对于从名字看不出用途的属性与方法（无论是否导出/公开），你也*应当*写文档，具体由评审者判断。

**例外**：只为让工具消费而导出的符号（例如 @NgModule 类）不需要注释。

### 8.6 类的注释

类的 JSDoc 注释应当给读者足够的信息，让他们知道如何使用这个类、何时使用这个类，以及正确使用该类所需的任何额外注意事项。构造函数上可以省略文字描述。

### 8.7 方法与函数的注释

如果从该方法 JSDoc 的其余部分、或从方法名与类型签名就能看明白，那么方法、参数与返回值的描述可以省略。

方法的描述以说明该方法做什么的动词短语开头。这个短语不是祈使句，而是用第三人称写的，仿佛前面隐含了「This method ...」。

<a id="parameter-property-comments"></a>
### 8.8 参数属性的注释

[参数属性](https://www.typescriptlang.org/docs/handbook/2/classes.html#parameter-properties)是指带 `private`、`protected`、`public` 或 `readonly` 之一修饰符前缀的构造函数参数。参数属性同时声明一个参数与一个实例属性，并隐式赋值。例如 `constructor(private readonly foo: Foo)` 声明构造函数接受一个参数 `foo`，同时也声明了一个私有 readonly 属性 `foo`，并在执行构造函数其余部分之前把参数赋给该属性。

要给这些字段写文档，请用 JSDoc 的 `@param` 标注。编辑器会在构造函数调用与属性访问处显示它的描述。

```
/** This class demonstrates how parameter properties are documented. */
class ParamProps {
  /**
   * @param percolator The percolator used for brewing.
   * @param beans The beans to brew.
   */
  constructor(
    private readonly percolator: Percolator,
    private readonly beans: CoffeeBean[]) {}
}
```

```
/** This class demonstrates how ordinary fields are documented. */
class OrdinaryClass {
  /** The bean that will be used in the next call to brew(). */
  nextBean: CoffeeBean;

  constructor(initialBean: CoffeeBean) {
    this.nextBean = initialBean;
  }
}
```

### 8.9 JSDoc 类型标注

在 TypeScript 源码里，JSDoc 的类型标注是多余的。不要在 `@param` 或 `@return` 块里声明类型；对于使用了 `implements`、`enum`、`private`、`override` 等关键字的代码，不要写 `@implements`、`@enum`、`@private`、`@override` 等。

### 8.10 写真正增加信息的注释

对未导出的符号来说，有时函数或参数的名字与类型已经够了。不过代码*通常*还是能从比变量名更多的文档中受益！

- 避免只是复述参数名与类型的注释，例如

  ```
  /** @param fooBarService The Bar service for the Foo application. */
  ```

- 正因如此，`@param` 与 `@return` 行只在能增加信息时才需要，其他情况下*可以*省略。

  ```
  /**
   * POSTs the request to start coffee brewing.
   * @param amountLitres The amount to brew. Must fit the pot size!
   */
  brew(amountLitres: number, logger: Logger) {
    // ...
  }
  ```

#### 8.10.1 调用函数时的注释

当方法名与参数值不足以传达参数含义时，就应当使用「参数名」注释。

加这些注释之前，先考虑把方法重构成接受一个接口并解构它，这样能大幅提升调用点的可读性。

「参数名」注释写在参数值之前，包含参数名与一个 `=` 后缀：

```
someFunction(obviousParam, /* shouldRender= */ true, /* name= */ 'hello');
```

现有代码可能使用旧式的参数名注释风格，即把这些注释放在参数值~之后~并省略 `=`。为了文件内的一致性，继续沿用这种风格是可以接受的。

```
someFunction(obviousParam, true /* shouldRender */, 'hello' /* name */);
```

### 8.11 把文档写在装饰器之前

当一个类、方法或属性同时有 `@Component` 这类装饰器和 JsDoc 时，请确保把 JsDoc 写在装饰器之前。

- 不要在装饰器与被修饰语句之间写 JsDoc。

  ```
  @Component({
    selector: 'foo',
    template: 'bar',
  })
  /** Component that prints "bar". */
  export class FooComponent {}
  ```

- 把 JsDoc 块写在装饰器之前。

  ```
  /** Component that prints "bar". */
  @Component({
    selector: 'foo',
    template: 'bar',
  })
  export class FooComponent {}
  ```

## 9 政策

### 9.1 一致性

本规范没有明确裁定的任何风格问题，就看同一文件里其他代码已经在怎么做（「保持一致」）。如果这样仍不能解决，考虑模仿同一目录下的其他文件。

全新的文件*必须*使用 Google 风格，无论同一包内其他文件风格如何。给非 Google 风格的文件添加新代码时，建议先重排现有代码，但须遵守[下文](#reformatting-existing-code)的建议。如果不做这次重排，新代码*应当*尽可能与同一文件里的现有代码保持一致，但*不得*违反风格指南。

<a id="reformatting-existing-code"></a>
#### 9.1.1 重排现有代码

你偶尔会碰到代码库里风格不正确的文件。它们可能来自收购，也可能是 Google 风格对某些问题表态之前写的，或者出于任何其他原因而属于非 Google 风格。

更新现有代码的风格时，请遵循以下准则。

1. 不要求把现有代码全部改成符合当前风格指南。重排现有代码是代码改动量与一致性之间的权衡。风格规则随时间演进，为保持符合而做这类微调会造成不必要的改动。不过，如果一个文件正在经历较大的改动，那就应当期望该文件符合 Google 风格。
2. 当心别让顺手做的风格修补搅乱了 CL 的重点。如果你发现自己做了很多对 CL 核心重点并不关键的风格改动，就把这些改动拆成一个单独的 CL。

### 9.2 弃用

用 `@deprecated` JSDoc 标注给弃用的方法、类或接口做标记。弃用注释必须包含简单、清晰的指引，告诉人们如何修正他们的调用点。

### 9.3 生成的代码：基本上豁免

构建过程生成的源码不要求符合 Google 风格。不过，任何会被手写源码引用的生成标识符必须遵守命名要求。作为特例，这类标识符允许包含下划线，这有助于避免与手写标识符冲突。

#### 9.3.1 风格指南的目标

总体而言，工程师通常最清楚自己的代码需要什么，所以如果有多个选项、且选择取决于具体情形，就应当把决定权下放。因此默认答案应当是「不加这条规则」。

以下是例外，也就是我们会有一些全局规则的理由。用下面几点来评估你的风格指南提案：

1. **代码应当避免已知会造成问题的模式，尤其是对刚接触这门语言的使用者。**
2. **跨项目的代码应当在无关的差异上也保持一致。**

   当两个选项在表面上等价时，我们应当考虑只选定一个，这样就不会无缘无故地分化演进，也能避免代码评审里无谓的争论。

   例子：

   - 名字的大小写风格。
   - `x as T` 语法与等价的 `<T>x` 语法（不允许）。
   - `Array<[number, number]>` 与 `[number, number][]`。
3. **代码应当长期可维护。**

   代码的寿命通常比原作者在其上工作的时间更长，而 TypeScript 团队必须让整个 Google 在未来继续运转。

   例子：

   - 我们用软件自动修改代码，所以代码要自动格式化，好让软件能满足空白规则。
   - 我们要求使用同一套编译器标志，这样某个 TS 库可以假定一组特定的标志来编写，而使用者总能安全地使用共享库。
   - 代码必须导入它用到的库（「strict deps」），这样依赖里的重构不会改变其使用者的依赖。
   - 我们要求使用者写测试。没有测试，我们就无法确信自己对语言所做的改动没有弄坏使用者。
4. **代码评审者应当专注于提升代码质量，而不是执行随意的规则。**

   如果你的规则能够实现为自动化检查，这通常是个好迹象。这也支持第 3 条原则。

   如果这件事真的没那么重要 —— 比如它只是语言里一个冷僻的角落，或者它避免的是一个不太可能出现的 bug —— 那大概就不值得加这条规则。

---

1. 命名空间导入常被称为「模块导入」（module imports）[↩](#fnref1)
2. 具名导入有时被称为「解构导入」（destructuring imports），因为它们使用与解构赋值相似的语法。[↩](#fnref2)
