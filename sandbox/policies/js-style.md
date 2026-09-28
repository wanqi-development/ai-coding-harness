# Google JavaScript 风格指南

> 译自 Google 的 [JavaScript Style Guide](https://google.github.io/styleguide/jsguide.html)，
> 原文以 [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/) 许可发布。
> 本文件是其简体中文译本，供编码与审查时查阅：章节编号与层级照原文，
> 代码示例、标识符与 JSDoc 标签一律保留英文原文。
>
> 原文页面开头的说明：**本指南已不再更新**，Google 建议迁移到 TypeScript，
> 并改用 [TypeScript 指南](https://google.github.io/styleguide/tsguide.html)。


## 1 引言

本文档是 Google 对 JavaScript 语言源码编码规范的**完整**定义。一个 JavaScript 源文件符合 Google 风格，当且仅当它遵守本文档中的全部规则。

与其他编程风格指南一样，本文涉及的范围不止格式这类审美问题，还包括其他类型的约定与编码规范。不过本文主要着眼于我们普遍遵循的硬性规则，避免给出无法明确执行的建议（无论由人来执行还是由工具来执行）。

### 1.1 术语说明

在本文档中，除非另有说明：

1. *注释*（comment）一词始终指*实现*注释。我们不用「文档注释」这个说法，人类可读的文本与 `/** … */` 内机器可读的标注，统称 **JSDoc**。
2. 本风格指南在使用*必须*（must）、*不得*（must not）、*应当*（should）、*不应*（should not）、*可以*（may）这些说法时，采用 [RFC 2119](http://tools.ietf.org/html/rfc2119) 的术语含义。*优先*（prefer）与*避免*（avoid）分别对应 *should* 与 *should not*。祈使句与陈述句是规定性的，对应 *must*。

其他「术语说明」会在文中各处零散出现。

### 1.2 指南说明

本文档中的示例代码**不具备规范效力**。也就是说，示例虽然符合 Google 风格，但未必是表达该段代码的唯一合规范写法。示例中可选的格式选择不得作为规则强制执行。

## 2 源文件基础

### 2.1 文件名

文件名必须全部小写，可以包含下划线（`_`）或短横线（`-`），但不得含其他标点。遵循你的项目所用的约定。文件名的扩展名必须是 `.js`。

### 2.2 文件编码：UTF-8

源文件以 **UTF-8** 编码。

### 2.3 特殊字符

#### 2.3.1 空白字符

除行终止符序列以外，ASCII 水平空格字符（0x20）是源文件中唯一出现的空白字符。这意味着

1. 字符串字面量里的其他空白字符一律写成转义形式，且
2. 缩进**不得**使用制表符。

#### 2.3.2 特殊转义序列

凡有特殊转义序列的字符（`\'`、`\"`、`\\`、`\b`、`\f`、`\n`、`\r`、`\t`、`\v`），一律使用该序列，而不用对应的数字转义（如 `\x0a`、`\u000a` 或 `\u{a}`）。遗留的八进制转义一律不用。

<a id="non-ascii-characters"></a>
#### 2.3.3 非 ASCII 字符

对于其余非 ASCII 字符，既可以用实际的 Unicode 字符（如 `∞`），也可以用等价的十六进制或 Unicode 转义（如 `\u221e`），只看哪种写法让代码**更易读、更易懂**。

提示：使用 Unicode 转义时，乃至偶尔在使用实际 Unicode 字符时，加一条解释性注释往往很有帮助。

```
/* Best: perfectly clear even without a comment. */
const units = 'μs';

/* Allowed: but unnecessary as μ is a printable character. */
const units = '\u03bcs'; // 'μs'

/* Good: use escapes for non-printable characters with a comment for clarity. */
return '\ufeff' + content;  // Prepend a byte order mark.
```

```
/* Poor: the reader has no idea what character this is. */
const units = '\u03bcs';
```

提示：绝不要仅仅因为担心某些程序处理不好非 ASCII 字符，就把代码写得更难读。真出现那种情况，那些程序是**坏的**，必须被**修好**。

<a id="source-file-structure"></a>
## 3 源文件结构

所有新的源文件都应当要么是 `goog.module` 文件（一个包含 `goog.module` 调用的文件），要么是 ECMAScript（ES）模块（使用 `import` 和 `export` 语句）。

文件由以下内容组成，**按顺序**：

1. 许可证或版权信息（如果存在）
2. `@fileoverview` JSDoc（如果存在）
3. `goog.module` 语句（如果是 `goog.module` 文件）
4. ES `import` 语句（如果是 ES 模块）
5. `goog.require` 和 `goog.requireType` 语句
6. 文件的实现

**恰好一个空行**分隔每个存在的部分，但文件的实现除外，它前面可以有 1 个或 2 个空行。

### 3.1 许可证或版权信息（如果存在）

如果许可证或版权信息属于某个文件，它就放在这里。

### 3.2 `@fileoverview` JSDoc（如果存在）

格式化规则见 [7.5 顶层/文件级注释](#jsdoc-top-file-level-comments)。

<a id="file-goog-module"></a>
### 3.3 `goog.module` 语句

所有 `goog.module` 文件都必须在一行内声明恰好一个 `goog.module` 名称：包含 `goog.module` 声明的行不得折行，因此它们是 80 列限制的例外。

`goog.module` 的整个参数定义了命名空间。它是包名（一个反映代码所在目录结构片段的标识符）加上（可选地）它所定义的主类/枚举/接口，以 `lowerCamelCase` 拼接在末尾。

示例：

```
goog.module('search.urlHistory.urlHistoryService');
```

#### 3.3.1 层级

模块命名空间绝不能命名为另一个模块命名空间的*直接*子级。

不允许：

```
goog.module('foo.bar');   // 'foo.bar.qux' would be fine, though
goog.module('foo.bar.baz');
```

目录层级反映命名空间层级，因此嵌套更深的子级是更高层级父目录的子目录。注意这意味着「父级」命名空间组的所有者必然知晓所有子命名空间，因为它们存在于同一个目录中。

#### 3.3.2 `goog.module.declareLegacyNamespace`

这个单一的 `goog.module` 语句后面可以可选地跟一个对 `goog.module.declareLegacyNamespace();` 的调用。尽可能避免使用 `goog.module.declareLegacyNamespace()`。

示例：

```
goog.module('my.test.helpers');
goog.module.declareLegacyNamespace();
goog.setTestOnly();
```

`goog.module.declareLegacyNamespace` 的存在是为了便于从传统的基于对象层级的命名空间过渡，但它带有一些命名限制。由于子模块名必须在父命名空间之后创建，这个名字**不得**是任何其他 `goog.module` 的子级或父级（例如，`goog.module('parent');` 和 `goog.module('parent.child');` 不能同时安全存在，`goog.module('parent');` 和 `goog.module('parent.child.grandchild');` 也不能）。

<a id="file-goog-module-exports"></a>
#### 3.3.3 `goog.module` 导出

类、枚举、函数、常量和其他符号通过 `exports` 对象导出。导出的符号可以直接定义在 `exports` 对象上，也可以在本地声明后单独导出。只有当符号打算在模块外部使用时才会被导出。未导出的模块本地符号不声明为 `@private`。导出符号和模块本地符号没有规定的排序。

示例：

```
const /** !Array<number> */ exportedArray = [1, 2, 3];

const /** !Array<number> */ moduleLocalArray = [4, 5, 6];

/** @return {number} */
function moduleLocalFunction() {
  return moduleLocalArray.length;
}

/** @return {number} */
function exportedFunction() {
  return moduleLocalFunction() * 2;
}

exports = {exportedArray, exportedFunction};
```

```
/** @const {number} */
exports.CONSTANT_ONE = 1;

/** @const {string} */
exports.CONSTANT_TWO = 'Another constant';
```

不要将 `exports` 对象标注为 `@const`，因为编译器已经把它视为常量。

```
/** @const */
exports = {exportedFunction};
```

不要使用默认导出，因为它们不容易转换为 ES 模块语义。

```
exports = FancyClass;
```

### 3.4 ES 模块

ES 模块是使用 `import` 和 `export` 关键字的文件。

<a id="es-module-imports"></a>
#### 3.4.1 导入

导入语句不得折行，因此它们是 80 列限制的例外。

##### 3.4.1.1 导入路径

ES 模块文件必须使用 `import` 语句来导入其他 ES 模块文件。不要 `goog.require` 另一个 ES 模块。

```
import './sideeffects.js';

import * as goog from '../closure/goog/goog.js';
import * as parent from '../parent.js';

import {name} from './sibling.js';
```

###### 3.4.1.1.1 导入路径中的文件扩展名

在导入路径中 `.js` 文件扩展名不是可选的，必须始终包含。

```
import '../directory/file';
```

```
import '../directory/file.js';
```

##### 3.4.1.2 多次导入同一个文件

不要多次导入同一个文件。这会让一个文件的整体导入关系难以确定。

```
// Imports have the same path, but since it doesn't align it can be hard to see.
import {short} from './long/path/to/a/file.js';
import {aLongNameThatBreaksAlignment} from './long/path/to/a/file.js';
```

##### 3.4.1.3 命名导入

###### 3.4.1.3.1 命名模块导入

模块导入名（`import * as name`）是派生自被导入文件名的 `lowerCamelCase` 名称。

```
import * as fileOne from '../file-one.js';
import * as fileTwo from '../file_two.js';
import * as fileThree from '../filethree.js';
```

```
import * as libString from './lib/string.js';
import * as math from './math/math.js';
import * as vectorMath from './vector/math.js';
```

有些库可能常用违反此命名方案的命名空间导入前缀，但极其普遍的开源用法使这种违规风格更易读。目前唯一属于此例外的库是 [threejs](https://threejs.org/)，它使用 `THREE` 前缀。

###### 3.4.1.3.2 命名默认导入

默认导入名派生自被导入的文件名，并遵循 [6.2 按标识符类型划分的规则](#naming-rules-by-identifier-type) 中的规则。

```
import MyClass from '../my-class.js';
import myFunction from '../my_function.js';
import SOME_CONSTANT from '../someconstant.js';
```

注意：一般来说这不应发生，因为本风格指南禁止默认导出，见 [3.4.2.1 具名导出与默认导出](#named-vs-default-exports)。默认导入仅用于导入不符合本风格指南的模块。

###### 3.4.1.3.3 命名具名导入

一般来说，通过具名导入（`import {name}`）导入的符号应当保持相同的名称。避免为导入起别名（`import {SomeThing as SomeOtherThing}`）。优先使用模块导入（`import *`）或重命名导出本身来修复名称冲突。

```
import * as bigAnimals from './biganimals.js';
import * as domesticatedAnimals from './domesticatedanimals.js';

new bigAnimals.Cat();
new domesticatedAnimals.Cat();
```

如果需要重命名具名导入，那么在生成的别名中使用被导入模块文件名或路径的组成部分。

```
import {Cat as BigCat} from './biganimals.js';
import {Cat as DomesticatedCat} from './domesticatedanimals.js';

new BigCat();
new DomesticatedCat();
```

#### 3.4.2 导出

只有当符号打算在模块外部使用时才会被导出。未导出的模块本地符号不声明为 `@private`。导出符号和模块本地符号没有规定的排序。

<a id="named-vs-default-exports"></a>
##### 3.4.2.1 具名导出与默认导出

在所有代码中都使用具名导出。你可以将 `export` 关键字应用于一个声明，或者使用 `export {name};` 语法。

不要使用默认导出。导入模块必须为这些值给出名称，这可能导致各模块之间的命名不一致。

```
// Do not use default exports:
export default class Foo { ... } // BAD!
```

```
// Use named exports:
export class Foo { ... }
```

```
// Alternate style named exports:
class Foo { ... }

export {Foo};
```

##### 3.4.2.2 导出的可变性

导出的变量不得在模块初始化之外被修改。

如果需要修改，有替代方案，包括导出一个指向具有可变字段的对象的常量引用，或者为可变数据导出访问器函数。

```
// Bad: both foo and mutateFoo are exported and mutated.
export let /** number */ foo = 0;

/**
 * Mutates foo.
 */
export function mutateFoo() {
  ++foo;
}

/**
 * @param {function(number): number} newMutateFoo
 */
export function setMutateFoo(newMutateFoo) {
  // Exported classes and functions can be mutated!
  mutateFoo = () => {
    foo = newMutateFoo(foo);
  };
}
```

```
// Good: Rather than export the mutable variables foo and mutateFoo directly,
// instead make them module scoped and export a getter for foo and a wrapper for
// mutateFooFunc.
let /** number */ foo = 0;
let /** function(number): number */ mutateFooFunc = (foo) => foo + 1;

/** @return {number} */
export function getFoo() {
  return foo;
}

export function mutateFoo() {
  foo = mutateFooFunc(foo);
}

/** @param {function(number): number} mutateFoo */
export function setMutateFoo(mutateFoo) {
  mutateFooFunc = mutateFoo;
}
```

<a id="es-module-export-from"></a>
##### 3.4.2.3 export from

`export from` 语句不得折行，因此它们是 80 列限制的例外。这适用于两种 `export from` 形式。

```
export {specificName} from './other.js';
export * from './another.js';
```

#### 3.4.3 ES 模块中的循环依赖

不要在 ES 模块之间创建循环，即使 ECMAScript 规范允许这样做。注意，使用 `import` 和 `export` 语句都可能创建循环。

```
// a.js
import './b.js';
```

```
// b.js
import './a.js';

// `export from` can cause circular dependencies too!
export {x} from './c.js';
```

```
// c.js
import './b.js';

export let x;
```

#### 3.4.4 与 Closure 互操作

##### 3.4.4.1 引用 goog

要引用 Closure 的 `goog` 命名空间，导入 Closure 的 `goog.js`。

```
import * as goog from '../closure/goog/goog.js';

const {compute} = goog.require('a.name');

export const CONSTANT = compute();
```

`goog.js` 只导出全局 `goog` 中可用于 ES 模块的那部分属性。

##### 3.4.4.2 ES 模块中的 goog.require

`goog.require` 在 ES 模块中的工作方式与在 `goog.module` 文件中相同。你可以 require 任何 Closure 命名空间符号（即由 `goog.provide` 或 `goog.module` 创建的符号），`goog.require` 将返回该值。

```
import * as goog from '../closure/goog/goog.js';
import * as anEsModule from './anEsModule.js';

const GoogPromise = goog.require('goog.Promise');
const myNamespace = goog.require('my.namespace');
```

##### 3.4.4.3 在 ES 模块中声明 Closure 模块 ID

`goog.declareModuleId` 可以在 ES 模块内使用，以声明一个类似 `goog.module` 的模块 ID。这意味着这个模块 ID 可以被 `goog.require`、`goog.module.get` 等使用，就好像它是一个没有调用 `goog.module.declareLegacyNamespace` 的 `goog.module`。它不会把这个模块 ID 创建为全局可用的 JavaScript 符号。

对来自 `goog.declareModuleId` 的模块 ID 执行 `goog.require`（或 `goog.module.get`）将始终返回模块对象（就像被 `import *` 了一样）。因此，`goog.declareModuleId` 的参数应当始终以 `lowerCamelCaseName` 结尾。

注意：在 ES 模块中调用 `goog.module.declareLegacyNamespace` 是错误的，它只能从 `goog.module` 文件中调用。没有直接将「旧式」命名空间与 ES 模块关联的方法。

`goog.declareModuleId` 应当只用于就地升级 Closure 文件到 ES 模块，此时使用具名导出。

```
import * as goog from '../closure/goog.js';

goog.declareModuleId('my.esm');

export class Class {};
```

### 3.5 `goog.setTestOnly`

在 `goog.module` 文件中，`goog.module` 语句以及（如果存在）`goog.module.declareLegacyNamespace()` 语句后面可以可选地跟一个对 `goog.setTestOnly()` 的调用。

在 ES 模块中，`import` 语句后面可以可选地跟一个对 `goog.setTestOnly()` 的调用。

<a id="file-goog-require"></a>
### 3.6 `goog.require` 和 `goog.requireType` 语句

导入通过 `goog.require` 和 `goog.requireType` 语句完成。由 `goog.require` 语句导入的名称既可以在代码中使用，也可以在类型标注中使用，而由 `goog.requireType` 导入的名称只能在类型标注中使用。

`goog.require` 和 `goog.requireType` 语句构成一个无空行的连续块。该块跟在 `goog.module` 声明之后，[以一个空行](#source-file-structure)分隔。`goog.require` 或 `goog.requireType` 的整个参数是由另一个文件中的 `goog.module` 定义的命名空间。`goog.require` 和 `goog.requireType` 语句不得出现在文件中的任何其他地方。

每个 `goog.require` 或 `goog.requireType` 都赋值给单个常量别名，或者解构为若干个常量别名。这些别名是在类型标注或代码中引用依赖的唯一可接受方式。完全限定命名空间不得在任何地方使用，除了作为 `goog.require` 或 `goog.requireType` 的参数。

**例外**：在 externs 文件中声明的类型、变量和函数必须在类型标注和代码中使用其完全限定名。

当 `goog.require` 赋值给单个常量别名时，它必须与被导入模块命名空间的最后一个点分隔组件匹配。

**例外**：在某些情况下，可以使用命名空间的额外组件来构成更长的别名。生成的别名必须保留原标识符的大小写，以便它仍能正确标识其类型。较长的别名可用于消除原本相同的别名的歧义，或者在显著提高可读性时使用。此外，必须使用较长的别名来防止遮蔽原生类型，例如 `Element`、`Event`、`Error`、`Map` 和 `Promise`（更完整的列表见 MDN 上的 [Standard Built-in Objects](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects) 和 [Web APIs](https://developer.mozilla.org/en-US/docs/Web/API)）。

重命名解构别名时，冒号后必须跟一个空格，如 [4.6.2 水平空白](#formatting-horizontal-whitespace) 所要求。

一个文件不应同时包含针对同一命名空间的 `goog.require` 和 `goog.requireType` 语句。如果被导入的名称既用于代码又用于类型标注，则应当由单个 `goog.require` 语句导入。

如果某个模块仅因其副作用而被导入，该调用必须是 `goog.require`（不是 `goog.requireType`）并且可以省略赋值。必须有一条注释解释为什么需要这样做，并抑制编译器警告。

这些行按以下规则排序：所有左侧带名称的 require 排在最前面，按这些名称的字母顺序排序。然后是解构 require，同样按左侧的名称排序。最后是任何独立的 require 调用（通常这些是仅为副作用而导入的模块）。

提示：不必记住这个顺序并手动强制执行。你可以依靠 IDE 来报告排序不正确的 require。

如果较长的别名或模块名会导致某一行超过 80 列限制，该行**不得**折行：require 行是 80 列限制的例外。

示例：

```
// Standard alias style.
const asserts = goog.require('goog.asserts');
// Namespace-based alias used to disambiguate.
const testingAsserts = goog.require('goog.testing.asserts');
// Standard destructuring into aliases.
const {MyClass} = goog.require('some.package');
const {MyType} = goog.requireType('other.package');
const {clear, clone} = goog.require('goog.array');
const {Rgb} = goog.require('goog.color');
// Namespace-based destructuring into aliases used to disambiguate.
const {MyClass: NsMyClass} = goog.require('other.ns');
const {SomeType: FooSomeType} = goog.requireType('foo.types');
const {clear: objectClear, clone: objectClone} = goog.require('goog.object');
// Namespace-based destructuring into aliases used to prevent masking native type.
const {Element: RendererElement} = goog.require('web.renderer');
// Out of sequence namespace-based aliases used to improve readability.
// Also, require lines longer than 80 columns must not be wrapped.
const {SomeDataStructure: SomeDataStructureModel} = goog.requireType('identical.package.identifiers.models');
const {SomeDataStructure: SomeDataStructureProto} = goog.require('proto.identical.package.identifiers');
// goog.require without an alias in order to trigger side effects.
/** @suppress {extraRequire} Initializes MyFramework. */
goog.require('my.framework.initialization');
```

不推荐：

```
// Some legacy code uses a "default export" style to export a single class, enum,
// record type, etc. Do not use this pattern in new JS.
// When using a "default export", prefer destructuring into aliases.
const MyClass = goog.require('some.package.MyClass');
const MyType = goog.requireType('some.package.MyType');
```

```
// If necessary to disambiguate, prefer PackageClass over SomeClass as it is
// closer to the format of the module name.
const SomeClass = goog.require('some.package.Class');
```

不允许：

```
// Extra terms must come from the namespace.
const MyClassForBizzing = goog.require('some.package.MyClass');
// Alias must include the entire final namespace component.
const MyClass = goog.require('some.package.MyClassForBizzing');
// Alias must not mask native type (should be `const JspbMap` here).
const Map = goog.require('jspb.Map');
// Don't break goog.require lines over 80 columns.
const SomeDataStructure =
    goog.require('proto.identical.package.identifiers.SomeDataStructure');
// Alias must be based on the namespace.
const randomName = goog.require('something.else');
// Missing a space after the colon.
const {Foo:FooProto} = goog.require('some.package.proto.Foo');
// goog.requireType without an alias.
goog.requireType('some.package.with.a.Type');

/**
 * @param {!some.unimported.Dependency} param All external types used in JSDoc
 *     annotations must be goog.require'd, unless declared in externs.
 */
function someFunction(param) {
  // goog.require lines must be at the top level before any other code.
  const alias = goog.require('my.long.name.alias');
  // ...
}
```

### 3.7 文件的实现

实际实现跟在所有依赖信息声明之后（以至少一个空行分隔）。

这可以包含任何模块本地声明（常量、变量、类、函数等），以及任何导出的符号。

## 4 格式

**术语说明**：*块状结构*指类、函数、方法的主体，或由花括号分隔的代码块。注意，根据 [5.2 数组字面量](#features-array-literals) 和 [5.3 对象字面量](#features-object-literals)，任何数组或对象字面量都可以选择性地按照块状结构来处理。

提示：使用 `clang-format`。JavaScript 社区已投入精力，确保 clang-format 在 JavaScript 文件上「做正确的事」。`clang-format` 与多款流行编辑器有集成。

### 4.1 花括号

#### 4.1.1 所有控制结构都使用花括号

所有控制结构（即 `if`、`else`、`for`、`do`、`while`，以及任何其他控制结构）都必须使用花括号，即使主体只包含一条语句。非空块的第一条语句必须另起一行。

不允许：

```
if (someVeryLongCondition())
  doSomething();

for (let i = 0; i < foo.length; i++) bar(foo[i]);
```

**例外**：若一条简单的 if 语句能完整地放在一行内且无需折行（并且没有 else），当它能提高可读性时，可以放在一行内并省略花括号。这是控制结构可以省略花括号和换行的唯一情形。

```
if (shortCondition()) foo();
```

<a id="formatting-nonempty-blocks"></a>
#### 4.1.2 非空块：K&R 风格

*非空*块和块状结构的花括号遵循 Kernighan 和 Ritchie 风格（「[埃及花括号](https://blog.codinghorror.com/new-programming-jargon/#3)」）：

- 左花括号前不换行。
- 左花括号后换行。
- 右花括号前换行。
- 右花括号后换行，*如果*该花括号终止了一条语句，或函数、类语句的主体，或一个类方法。具体而言，如果该花括号后跟着 `else`、`catch`、`while`，或逗号、分号、右圆括号，则其后*不*换行。

示例：

```
class InnerClass {
  constructor() {}

  /** @param {number} foo */
  method(foo) {
    if (condition(foo)) {
      try {
        // Note: this might fail.
        something();
      } catch (err) {
        recover();
      }
    }
  }
}
```

<a id="formatting-empty-blocks"></a>
#### 4.1.3 空块：可以简洁

空块或块状结构*可以*在打开后立即关闭，中间没有任何字符、空格或换行（即 `{}`），**除非**它是*多块语句*（直接包含多个块的语句：`if`/`else` 或 `try`/`catch`/`finally`）的一部分。

示例：

```
function doNothing() {}
```

不允许：

```
if (condition) {
  // …
} else if (otherCondition) {} else {
  // …
}

try {
  // …
} catch (e) {}
```

<a id="formatting-block-indentation"></a>
### 4.2 块缩进：+2 个空格

每当打开一个新的块或块状结构，缩进就增加两个空格。当块结束时，缩进恢复到之前的缩进层级。缩进层级同时适用于块内的代码和注释。（参见 [4.1.2 非空块：K&R 风格](#formatting-nonempty-blocks) 中的示例。）

<a id="formatting-array-literals"></a>
#### 4.2.1 数组字面量：可选地「像块一样」

任何数组字面量都可以选择性地按照「块状结构」来格式化。例如，以下都是合法的（**并非**完整列表）：

```
const a = [
  0,
  1,
  2,
];

const b =
    [0, 1, 2];
```

```
const c = [0, 1, 2];

someMethod(foo, [
  0, 1, 2,
], bar);
```

允许其他组合，尤其是在强调元素之间的语义分组时，但不应用来仅仅为了减小较大数组的垂直尺寸。

#### 4.2.2 对象字面量：可选地「像块一样」

任何对象字面量都可以选择性地按照「块状结构」来格式化。与 [4.2.1 数组字面量：可选地「像块一样」](#formatting-array-literals) 适用相同的示例。例如，以下都是合法的（**并非**完整列表）：

```
const a = {
  a: 0,
  b: 1,
};

const b =
    {a: 0, b: 1};
```

```
const c = {a: 0, b: 1};

someMethod(foo, {
  a: 0, b: 1,
}, bar);
```

#### 4.2.3 类字面量

类字面量（无论是声明还是表达式）都像块一样缩进。不要在方法后，或在类*声明*的右花括号后添加分号（包含类*表达式*的语句——例如赋值——仍以分号结尾）。对于继承，除非父类是模板化的，否则 `extends` 关键字就已足够。模板化类型的子类必须在 `@extends` JSDoc 注解中显式指定模板类型，即使只是传递相同的模板名。

示例：

```
/** @template T */
class Foo {
  /** @param {T} x */
  constructor(x) {
    /** @type {T} */
    this.x = x;
  }
}

/** @extends {Foo<number>} */
class Bar extends Foo {
  constructor() {
    super(42);
  }
}

exports.Baz = class extends Bar {
  /** @return {number} */
  method() {
    return this.x;
  }
};
```

```
/** @extends {Bar} */ // <-- unnecessary @extends
exports.Baz = class extends Bar {
  /** @return {number} */
  method() {
    return this.x;
  }
};
```

#### 4.2.4 函数表达式

在函数调用的实参列表中声明匿名函数时，函数体比前面的缩进深度多缩进两个空格。

示例：

```
prefix.something.reallyLongFunctionName('whatever', (a1, a2) => {
  // Indent the function body +2 relative to indentation depth
  // of the 'prefix' statement one line above.
  if (a1.equals(a2)) {
    someOtherLongFunctionName(a1);
  } else {
    andNowForSomethingCompletelyDifferent(a2.parrot);
  }
});

some.reallyLongFunctionCall(arg1, arg2, arg3)
    .thatsWrapped()
    .then((result) => {
      // Indent the function body +2 relative to the indentation depth
      // of the '.then()' call.
      if (result) {
        result.use();
      }
    });
```

#### 4.2.5 switch 语句

与任何其他块一样，switch 块的内容缩进 +2。

在 switch 标签之后换行，且缩进层级增加 +2，就如同打开了一个块。如果词法作用域需要，可以使用显式的块。下一个 switch 标签恢复到先前的缩进层级，就如同关闭了一个块。

`break` 与其后的 case 分支之间的空行是可选的。

示例：

```
switch (animal) {
  case Animal.BANDERSNATCH:
    handleBandersnatch();
    break;

  case Animal.JABBERWOCK:
    handleJabberwock();
    break;

  default:
    throw new Error('Unknown animal');
}
```

### 4.3 语句

#### 4.3.1 每行一条语句

每条语句后都换行。

#### 4.3.2 必须有分号

每条语句都必须以分号结尾。禁止依赖自动分号插入。

### 4.4 列数上限：80

JavaScript 代码的列数上限为 80 个字符。除下文所述外，任何超过此上限的行都必须折行，如 [4.5 折行](#formatting-line-wrapping) 所述。

**例外：**

1. `goog.module`、`goog.require` 和 `goog.requireType` 语句（参见
   [3.3 `goog.module` 语句](#file-goog-module) 和 [3.6 `goog.require` 和 `goog.requireType` 语句](#file-goog-require)）。
2. ES module 的 `import` 和 `export from` 语句（参见
   [3.4.1 导入](#es-module-imports) 和 [3.4.2.3 export from](#es-module-export-from)）。
3. 遵守列数上限不可能或会妨碍可发现性的行。示例包括：
   - 在源码中应当可点击的长 URL。
   - 意图被复制粘贴的 shell 命令。
   - 可能需要整体复制或搜索的长字符串字面量（例如长文件路径）。

<a id="formatting-line-wrapping"></a>
### 4.5 折行

**术语说明**：*折行*指为了遵守列数上限而将一段代码拆成多行，而该段代码原本可以合法地放在一行内。

并不存在全面、确定的公式来表明在每种情况下*究竟*该如何折行。同一段代码常常有多种合法的折行方式。

注意：虽然折行的典型原因是避免超出列数上限，但即使实际上能放进列数上限内的代码，也可以由作者自行决定折行。

提示：提取一个方法或局部变量或许就能解决问题，而无需折行。

<a id="formatting-where-to-break"></a>
#### 4.5.1 在哪里断行

折行的首要原则是：优先在**更高的语法层级**处断行。

推荐：

```
currentEstimate =
    calc(currentEstimate + x * currentEstimate) /
        2.0;
```

不推荐：

```
currentEstimate = calc(currentEstimate + x *
    currentEstimate) / 2.0;
```

在上面的示例中，语法层级从高到低依次为：赋值、除法、函数调用、参数、数字常量。

运算符的折行规则如下：

1. 在运算符处断行时，断点位于符号之后。（注意这与 Google 风格中 Java 的做法不同。）
   1. 这不适用于「点」（`.`），它实际上不是运算符。
2. 方法名或构造函数名与其后的左圆括号（`(`）保持相连。
3. 逗号（`,`）与其前面的记号保持相连。
4. 绝不在 return 与其返回值之间添加换行，因为这会改变代码的含义。
5. 带类型名的 JSDoc 注解在「{」之后断行。这是必要的，因为带可选类型的注解（@const、@private、@param 等）不会扫描下一行。

> 注意：折行的首要目标是代码清晰，而不必然是行数最少的代码。

#### 4.5.2 续行至少缩进 +4 个空格

折行时，除第一行外的每一行（每条*续行*）都比原始行至少多缩进 +4，除非它属于块缩进规则的适用范围。

存在多条续行时，缩进可以在 +4 之外适当变化。通常，处于更深语法层级的续行缩进更大的 4 的倍数，且两行使用相同缩进层级当且仅当它们以语法上并列的元素开头。

[4.6.3 水平对齐：不鼓励](#formatting-horizontal-alignment) 讨论了用数量可变的空格将某些记号与前面几行对齐的这种不受鼓励的做法。

### 4.6 空白

#### 4.6.1 垂直空白

出现单个空行的情况：

1. 类或对象字面量中连续的方法之间
   1. 例外：对象字面量中两个连续属性定义之间的空行（两者之间没有其他代码）是可选的。此类空行按需用于创建字段的*逻辑分组*。
2. 方法体内，少量用于创建语句的*逻辑分组*。不允许在函数体的开头或结尾出现空行。
3. *可选地*在类或对象字面量中第一个方法之前或最后一个方法之后（既不鼓励也不反对）。
4. 如本文档其他章节所要求（例如
   [3.6 `goog.require` 和 `goog.requireType` 语句](#file-goog-require)）。

*多个*连续空行是允许的，但从不必需（也不鼓励）。

<a id="formatting-horizontal-whitespace"></a>
#### 4.6.2 水平空白

水平空白的使用取决于位置，分为三大类：*前导*（行首）、*尾随*（行尾）和*内部*。前导空白（即缩进）在其他地方讨论。尾随空白是禁止的。

除语言或其他风格规则要求之处，以及字面量、注释和 JSDoc 之外，单个内部 ASCII 空格还**仅**出现在以下位置。

1. 将任何保留字（如 `if`、`for` 或 `catch`，`function` 和 `super` 除外）与该行中紧随其后的左圆括号（`(`）分隔开。
2. 将任何保留字（如 `else` 或 `catch`）与该行中紧接其前的右花括号（`}`）分隔开。
3. 任何左花括号（`{`）之前，有两个例外：
   1. 作为函数第一个实参或数组字面量第一个元素的对象字面量之前（例如 `foo({a: [{c: d}]})`）。
   2. 模板展开中，因为语言禁止这样做（例如合法：
      `` `ab${1 + 2}cd` ``，非法：`` `xy$ {3}z` ``）。
4. 任何二元或三元运算符的两侧。
5. 逗号（`,`）或分号（`;`）之后。注意这两个字符之前*绝不*允许有空格。
6. 对象字面量中冒号（`:`）之后。
7. 开始行尾注释的双斜杠（`//`）的两侧。此处允许多个空格，但不要求。
8. 块注释开始字符之后以及结束字符的两侧（例如用于短式类型声明、类型转换和参数名注释：
   `this.foo = /** @type {number} */ (bar)`；或 `function(/** string */ foo)
   {`；或 `baz(/* buzz= */ true)`）。

<a id="formatting-horizontal-alignment"></a>
#### 4.6.3 水平对齐：不鼓励

**术语说明**：*水平对齐*是指在代码中添加数量可变的额外空格，目的是使某些记号直接位于前几行某些其他记号的正下方。

这种做法是允许的，但 Google 风格**一般不鼓励**。甚至不要求在已经使用过水平对齐的地方*维持*水平对齐。

下面是一个没有对齐的示例，随后是一个有对齐的示例。两者都允许，但后者不受鼓励：

```
{
  tiny: 42, // this is great
  longer: 435, // this too
};

{
  tiny:   42,  // permitted, but future edits
  longer: 435, // may leave it unaligned
};
```

提示：对齐有助于可读性，但会给未来的维护带来问题。设想未来有一处改动只需触及一行。该改动可能使原本令人满意的格式变得凌乱，而这是允许的。更常见的情况是，它会促使编码者（也许是你）一并调整相邻行的空白，可能引发一连串的重新格式化。这一行改动如今有了「爆炸半径」。最坏的情况下这会导致无谓的琐碎工作，但即便最好的情况下，它也会破坏版本历史信息、拖慢审查者并加剧合并冲突。

#### 4.6.4 函数实参

优先将所有函数实参放在与函数名同一行。如果这样做会超出 80 列上限，则实参必须以可读的方式折行。为了节省空间，你可以尽可能接近 80 列地折行，或让每个实参单独占一行以增强可读性。缩进应为四个空格。与圆括号对齐是允许的，但不鼓励。以下是实参折行最常见的模式：

```
// Arguments start on a new line, indented four spaces. Preferred when the
// arguments don't fit on the same line with the function name (or the keyword
// "function") but fit entirely on the second line. Works with very long
// function names, survives renaming without reindenting, low on space.
doSomething(
    descriptiveArgumentOne, descriptiveArgumentTwo, descriptiveArgumentThree) {
  // …
}

// If the argument list is longer, wrap at 80. Uses less vertical space,
// but violates the rectangle rule and is thus not recommended.
doSomething(veryDescriptiveArgumentNumberOne, veryDescriptiveArgumentTwo,
    tableModelEventHandlerProxy, artichokeDescriptorAdapterIterator) {
  // …
}

// Four-space, one argument per line.  Works with long function names,
// survives renaming, and emphasizes each argument.
doSomething(
    veryDescriptiveArgumentNumberOne,
    veryDescriptiveArgumentTwo,
    tableModelEventHandlerProxy,
    artichokeDescriptorAdapterIterator) {
  // …
}
```

### 4.7 分组圆括号：推荐使用

只有当作者和审查者一致认为没有合理可能使代码在缺少分组圆括号时被误解，且分组圆括号也不会让代码更易读时，才省略可选的分组圆括号。假设每位读者都记住了完整的运算符优先级表是*不*合理的。

不要在 `delete`、`typeof`、`void`、`return`、`throw`、`case`、`in`、`of` 或 `yield` 之后的整个表达式周围使用不必要的圆括号。

类型转换必须有圆括号：`/** @type {!Foo} */ (foo)`。

### 4.8 注释

本节讨论*实现注释*。JSDoc 在 [7 JSDoc](#jsdoc) 中单独讨论。

#### 4.8.1 块注释风格

块注释与周围代码缩进在同一层级。它们可以采用 `/* … */` 或 `//` 风格。对于多行 `/* … */` 注释，后续行必须以 `*` 开头，并与上一行的 `*` 对齐，以便在无需额外上下文的情况下让注释清晰可见。

```
/*
 * This is
 * okay.
 */

// And so
// is this.

/* This is fine, too. */
```

注释不用星号或其他字符画成的框包围。

不要将 JSDoc（`/** … */`）用于实现注释。

#### 4.8.2 参数名注释

当值和（方法名）不足以充分传达含义，且重构方法使其更清晰不可行时，就应当使用「参数名」注释。其首选格式是放在值之前并带「=」：

```
someFunction(obviousParam, /* shouldRender= */ true, /* name= */ 'hello');
```

为了与周围代码保持一致，你也可以不带「=」将它们放在值之后：

```
someFunction(obviousParam, true /* shouldRender */, 'hello' /* name */);
```

## 5 语言特性

JavaScript 包含许多可疑（甚至危险）的特性。本节界定了哪些特性可以使用、哪些不得使用，以及使用它们时的其他约束。

本风格指南未讨论的语言特性可以使用，但对其用法不作任何建议。

### 5.1 局部变量声明

#### 5.1.1 使用 `const` 和 `let`

所有局部变量都用 `const` 或 `let` 声明。默认使用 `const`，除非变量需要重新赋值。不得使用 `var` 关键字。

#### 5.1.2 每个声明一个变量

每个局部变量声明只声明一个变量：不使用 `let a = 1, b = 2;` 这类声明。

#### 5.1.3 需要时声明，尽快初始化

局部变量**不**习惯性地声明在其所在块或类块结构的开头。相反，局部变量在其首次使用点附近（合理范围内）声明，以尽量缩小其作用域，并尽快初始化。

#### 5.1.4 按需声明类型

JSDoc 类型标注既可以添加在声明的上一行，也可以在没有其他 JSDoc 时内联在变量名之前。

示例：

```
const /** !Array<number> */ data = [];

/**
 * Some description.
 * @type {!Array<number>}
 */
const data = [];
```

不允许混用内联与 JSDoc 两种风格：编译器只会处理第一个 JsDoc，内联标注会丢失。

```
/** Some description. */
const /** !Array<number> */ data = [];
```

提示：在很多情况下，编译器可以推断出模板化类型，却推断不出其参数。当初始化字面量或构造函数调用不包含任何模板参数类型的值（例如空数组、对象、`Map` 或 `Set`），或者变量在闭包中被修改时，尤其如此。在这些情况下，局部变量类型标注特别有用，因为否则编译器会把模板参数推断为 unknown。

<a id="features-array-literals"></a>
### 5.2 数组字面量

#### 5.2.1 使用尾随逗号

当最后一个元素与右方括号之间存在换行时，要包含尾随逗号。

示例：

```
const values = [
  'first value',
  'second value',
];
```

#### 5.2.2 不要使用变参的 `Array` 构造函数

当参数被添加或移除时，该构造函数容易出错。应改用字面量。

不允许：

```
const a1 = new Array(x1, x2, x3);
const a2 = new Array(x1, x2);
const a3 = new Array(x1);
const a4 = new Array();
```

除第三种情况外，以上写法都能按预期工作：如果 `x1` 是整数，那么 `a3` 是一个大小为 `x1` 且所有元素均为 `undefined` 的数组。如果 `x1` 是其他任何数字，则会抛出异常；如果是其他任何类型，则会得到单元素数组。

应改为

```
const a1 = [x1, x2, x3];
const a2 = [x1, x2];
const a3 = [x1];
const a4 = [];
```

在合适时，允许使用 `new Array(length)` 显式分配给定长度的数组。

#### 5.2.3 非数字属性

不要在数组上定义或使用非数字属性（`length` 除外）。应改用 `Map`（或 `Object`）。

#### 5.2.4 解构

数组字面量可以用在赋值语句左侧以执行解构（例如从单个数组或可迭代对象中解包多个值）。可以包含一个末尾的「rest」元素（`...` 与变量名之间不加空格）。如果元素未被使用，则应省略。

```
const [a, b, c, ...rest] = generateResults();
let [, b,, d] = someArray;
```

解构也可用于函数参数（注意参数名是必需的但会被忽略）。如果解构的数组参数是可选的，要始终指定 `[]` 作为默认值，并在左侧提供默认值：

```
/** @param {!Array<number>=} param1 */
function optionalDestructuring([a = 4, b = 2] = []) { … };
```

不允许：

```
function badDestructuring([a, b] = [4, 2]) { … };
```

提示：在把多个值打包进/解包出函数的参数或返回值时，只要可能就优先使用对象解构而非数组解构，因为这样可以给各个元素命名，并为每个元素指定不同的类型。

#### 5.2.5 展开运算符

数组字面量可以包含展开运算符（`...`），以把一个或多个其他可迭代对象中的元素展开。应当使用展开运算符，而不是使用 `Array.prototype` 的更别扭的写法。`...` 之后不加空格。

示例：

```
[...foo]   // preferred over Array.prototype.slice.call(foo)
[...foo, ...bar]   // preferred over foo.concat(bar)
```

<a id="features-object-literals"></a>
### 5.3 对象字面量

#### 5.3.1 使用尾随逗号

当最后一个属性与右花括号之间存在换行时，要包含尾随逗号。

#### 5.3.2 不要使用 `Object` 构造函数

虽然 `Object` 没有 `Array` 那样的问题，但为保持一致，仍然不允许使用。应改用对象字面量（`{}` 或 `{a: 0, b: 1, c: 2}`）。

<a id="features-objects-mixing-keys"></a>
#### 5.3.3 不要混用带引号和不带引号的键

对象字面量可以表示 *structs*（使用不带引号的键和/或 symbol），也可以表示 *dicts*（使用带引号和/或计算的键）。不要在一个对象字面量中混用这些键类型。

不允许：

```
{
  width: 42, // struct-style unquoted key
  'maxWidth': 43, // dict-style quoted key
}
```

这也适用于把属性名传给函数的情况，比如 `hasOwnProperty`。特别地，这样做会在编译后的代码中出错，因为编译器无法重命名/混淆该字符串字面量。

不允许：

```
/** @type {{width: number, maxWidth: (number|undefined)}} */
const o = {width: 42};
if (o.hasOwnProperty('maxWidth')) {
  ...
}
```

最好这样实现：

```
/** @type {{width: number, maxWidth: (number|undefined)}} */
const o = {width: 42};
if (o.maxWidth != null) {
  ...
}
```

#### 5.3.4 计算属性名

允许使用计算属性名（例如 `{['key' + foo()]: 42}`），它们被视为 dict 风格（带引号）的键（即不得与不带引号的键混用），除非计算属性是 [symbol](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Symbol)（例如 `[Symbol.iterator]`）。枚举值也可以用于计算键，但不应在同一字面量中与非枚举键混用。

<a id="features-objects-method-shorthand"></a>
#### 5.3.5 方法简写

可以使用方法简写（`{method() {… }}`）在对象字面量上定义方法，以替代紧跟 `function` 或箭头函数字面量的冒号。

示例：

```
return {
  stuff: 'candy',
  method() {
    return this.stuff;  // Returns 'candy'
  },
};
```

注意，方法简写或 `function` 中的 `this` 指向对象字面量自身，而箭头函数中的 `this` 指向对象字面量外部的作用域。

示例：

```
class {
  getObjectLiteral() {
    this.stuff = 'fruit';
    return {
      stuff: 'candy',
      method: () => this.stuff,  // Returns 'fruit'
    };
  }
}
```

#### 5.3.6 简写属性

允许在对象字面量上使用简写属性。

示例：

```
const foo = 1;
const bar = 2;
const obj = {
  foo,
  bar,
  method() { return this.foo + this.bar; },
};
assertEquals(3, obj.method());
```

<a id="features-objects-destructuring"></a>
#### 5.3.7 解构

对象解构模式可以用在赋值语句左侧，以执行解构并从单个对象中解包多个值。

解构对象也可以用作函数参数，但应尽可能保持简单：单层的不带引号简写属性。在参数解构中不得使用更深层次的嵌套和计算属性。默认值要写在解构参数的左侧（`{str = 'some default'} = {}`，而不是 `{str} = {str: 'some default'}`），如果解构对象本身是可选的，则必须默认为 `{}`。解构参数的 JSDoc 可以取任意名称（该名称未被使用，但编译器要求提供）。

示例：

```
/**
 * @param {string} ordinary
 * @param {{num: (number|undefined), str: (string|undefined)}=} param1
 *     num: The number of times to do something.
 *     str: A string to do stuff to.
 */
function destructured(ordinary, {num, str = 'some default'} = {}) {}
```

不允许：

```
/** @param {{x: {num: (number|undefined), str: (string|undefined)}}} param1 */
function nestedTooDeeply({x: {num, str}}) {};
/** @param {{num: (number|undefined), str: (string|undefined)}=} param1 */
function nonShorthandProperty({num: a, str: b} = {}) {};
/** @param {{a: number, b: number}} param1 */
function computedKey({a, b, [a + b]: c}) {};
/** @param {{a: number, b: string}=} param1 */
function nontrivialDefault({a, b} = {a: 2, b: 4}) {};
```

解构也可用于 `goog.require` 语句，这种情况下不得换行包裹：无论多长，整条语句都占一行（见 [3.6 `goog.require` 和 `goog.requireType` 语句](#file-goog-require)）。

#### 5.3.8 枚举

枚举通过在对象字面量上添加 `@enum` 标注来定义。枚举必须是模块局部的，或直接赋值给 `exports`，而不得嵌套在某个类型或对象之下。

枚举定义之后不得再添加其他属性。枚举必须是常量。所有枚举值都必须是字符串字面量或数字。

```
/**
 * Supported temperature scales.
 * @enum {string}
 */
const TemperatureScale = {
  CELSIUS: 'celsius',
  FAHRENHEIT: 'fahrenheit',
};

/**
 * An enum with two values.
 * @enum {number}
 */
const Value = {
  /** The value used shall have been the first. */
  FIRST_VALUE: 1,
  /** The second among two values. */
  SECOND_VALUE: 2,
};
```

对于字符串枚举，所有值都必须静态初始化，不得使用算术运算符、模板字符串替换、函数调用乃至变量引用来计算。

```
const ABSOLUTE_ZERO = '-273°F';

/**
 * Not supported computed values in string enum.
 * @enum {string}
 */
const TemperatureInFahrenheit = {
  MIN_POSSIBLE: ABSOLUTE_ZERO,
  ZERO_FAHRENHEIT: 0 + '°F',
  ONE_FAHRENHEIT: `${Values.FIRST_VALUE}°F`,
  TWO_FAHRENHEIT: Values.SECOND_VALUE + '°F',
  SOME_FAHRENHEIT: getTemperatureInFahrenheit() + '°F',
};
```

注意：尽管 TypeScript 支持更多用于枚举值的模式（例如 `A: 'a'+'b'` 等），把枚举值限制为只允许字符串字面量和数字是为了便于迁移到 TypeScript。对于复杂的值，考虑使用不带 `@enum` 的 const 对象。

<a id="features-classes"></a>
### 5.4 类

#### 5.4.1 构造函数

构造函数是可选的。子类构造函数在设置任何字段或以其他方式访问 `this` 之前，必须先调用 `super()`。接口应当在构造函数中声明非方法属性。

#### 5.4.2 字段

在构造函数中定义具体对象的所有字段（即除方法之外的所有属性）。用 `@const` 标注从不重新赋值的字段（这些字段不必深度不可变）。用适当的可见性标注（`@private`、`@protected`、`@package`）标注非公开字段。`@private` 字段的名称可以选择以下划线结尾。不得在构造函数中的嵌套作用域内定义字段，也不得在具体类的 `prototype` 上定义字段。

示例：

```
class Foo {
  constructor() {
    /** @private @const {!Bar} */
    this.bar_ = computeBar();

    /** @protected @const {!Baz} */
    this.baz = computeBaz();
  }
}
```

提示：构造函数结束后，绝不应向实例添加属性或从实例移除属性，因为这会显著妨碍虚拟机（VM）优化的能力。如有必要，稍后才初始化的字段应在构造函数中显式设为 `undefined`，以防止后续的形状变化。为对象添加 `@struct` 会检查未声明的属性没有被添加/访问。类默认会加上这一项。

#### 5.4.3 计算属性

在类中，只有当属性是 symbol 时才可以使用计算属性。不允许使用 dict 风格的属性（即 [5.3.3 不要混用带引号和不带引号的键](#features-objects-mixing-keys) 中定义的带引号或计算的非 symbol 键）。对于任何逻辑上可迭代的类，都应当定义 `[Symbol.iterator]` 方法。除此之外，应当谨慎使用 `Symbol`。

提示：要小心使用其他任何内置 symbol（例如 `Symbol.isConcatSpreadable`），因为编译器不会为它们做 polyfill，因此在较旧的浏览器中无法工作。

#### 5.4.4 静态方法

在不影响可读性的情况下，优先使用模块局部函数而非私有静态方法。

代码不应依赖静态方法的动态分派，因为这会妨碍 Closure Compiler 优化。静态方法只应在基类自身上调用。不应在包含动态实例的变量上调用静态方法，该实例可能是构造函数，也可能是子类构造函数（如果这样做，必须用 `@nocollapse` 定义），并且不得在未自行定义该方法的子类上直接调用。不要在静态方法中访问 `this`。

不允许：

```
// Context for the examples below; by itself this code is allowed.
class Base {
  /** @nocollapse */ static foo() {}
}
class Sub extends Base {}

// discouraged: don't call static methods dynamically
function callFoo(cls) { cls.foo(); }

// Disallowed: don't call static methods on subclasses that don't define it themselves
Sub.foo();

// Disallowed: don't access this in static methods.
class Clazz {
  static foo() {
    return this.staticField;
  }
}
Class.staticField = 1;
```

<a id="features-classes-old-style"></a>
#### 5.4.5 旧式类声明

虽然优先使用 ES6 类，但在某些情况下 ES6 类可能不可行。例如：

1. 如果已经存在或将会存在无法立即改为使用 ES6 类语法的子类（包括会创建子类的框架）。如果这样的类改用 ES6 语法，那么所有不使用 ES6 类语法的下游子类都需要修改。
2. 需要在调用父类构造函数之前获得已知 `this` 值的框架，因为使用 ES6 父类的构造函数在 `super` 调用返回之前无法访问实例的 `this` 值。

在所有其他方面，本风格指南仍然适用于这类代码：在合适时都应使用 `let`、`const`、默认参数、rest 和箭头函数。

`goog.defineClass` 允许使用类似 ES6 类语法的类式定义：

```
let C = goog.defineClass(S, {
  /**
   * @param {string} value
   */
  constructor(value) {
    S.call(this, 2);
    /** @const */
    this.prop = value;
  },

  /**
   * @param {string} param
   * @return {number}
   */
  method(param) {
    return 0;
  },
});
```

另外，虽然所有新代码都应当优先使用 `goog.defineClass`，但也允许使用更传统的语法。

```
/**
  * @constructor @extends {S}
  * @param {string} value
  */
function C(value) {
  S.call(this, 2);
  /** @const */
  this.prop = value;
}
goog.inherits(C, S);

/**
 * @param {string} param
 * @return {number}
 */
C.prototype.method = function(param) {
  return 0;
};
```

如果存在父类，实例属性应当在调用父类构造函数之后于构造函数中定义。方法应当定义在构造函数的原型上。

正确定义构造函数原型层级比乍看起来要难！因此，最好使用 [the Closure Library](http://code.google.com/closure/library/) 中的 `goog.inherits`。

#### 5.4.6 不要直接操作 `prototype`

`class` 关键字能提供比定义 `prototype` 属性更清晰、更易读的类定义。普通实现代码没有理由操作这些对象，不过它们在 [5.4.5 旧式类声明](#features-classes-old-style) 中定义的类定义中仍然有用。明确禁止 mixin 以及修改内置对象的原型。

**例外**：框架代码（例如 Polymer 或 Angular）可能需要使用 `prototype`，此时不应为了避开它而求助于更糟糕的变通做法。

#### 5.4.7 Getter 和 Setter

不要使用 [JavaScript getter 和 setter 属性](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Functions/get)。它们可能出人意料且难以推理，并且编译器对它们的支持有限。应改用普通方法。

**例外**：有些情况下定义 getter 或 setter 不可避免（例如 Angular 和 Polymer 等数据绑定框架，或为了兼容无法调整的外部 API）。仅在这些情况下，可以*谨慎*使用 getter 和 setter，前提是它们使用 `get` 和 `set` 方法简写关键字或 `Object.defineProperties` 定义（而不是 `Object.defineProperty`，后者会妨碍属性重命名）。getter **不得**改变可观察状态。

不允许：

```
class Foo {
  get next() { return this.nextId++; }
}
```

#### 5.4.8 覆写 toString

可以覆写 `toString` 方法，但它必须始终成功，且绝不能有可见的副作用。

提示：尤其要小心在 toString 中调用其他方法，因为异常情况可能导致无限循环。

#### 5.4.9 接口

接口可以用 `@interface` 或 `@record` 声明。用 `@record` 声明的接口可以由类或对象字面量显式（即通过 `@implements`）或隐式实现。

接口上的所有方法都必须是非静态的，方法体必须是空块。字段必须在类的构造函数中声明为未初始化的成员。

示例：

```
/**
 * Something that can frobnicate.
 * @record
 */
class Frobnicator {
  constructor() {
    /** @type {number} The number of attempts before giving up. */
    this.attempts;
  }

  /**
   * Performs the frobnication according to the given strategy.
   * @param {!FrobnicationStrategy} strategy
   */
  frobnicate(strategy) {}
}
```

#### 5.4.10 抽象类

在合适时使用抽象类。抽象类和方法必须用 `@abstract` 标注。不要使用 `goog.abstractMethod`。参见 [abstract classes and methods](https://github.com/google/closure-compiler/wiki/@abstract-classes-and-methods)。

#### 5.4.11 不要创建静态容器类

不要为了命名空间的目的而使用只含静态方法或属性的容器类。

```
// container.js
// Bad: Container is an exported class that has only static methods and fields.
class Container {
  /** @return {number} */
  static bar() {
    return 1;
  }
}

/** @const {number} */
Container.FOO = 1;

exports = {Container};
```

应改为导出单独的常量和函数：

```
/** @return {number} */
exports.bar = () => {
  return 1;
}

/** @const {number} */
exports.FOO = 1;
```

#### 5.4.12 不要定义嵌套命名空间

不要在另一个模块局部名称上定义嵌套类型（例如类、typedef、枚举、接口）。

```
// foo.js
goog.module('my.namespace');

class Foo {...}

Foo.Bar = class {...};

/** @enum {number} */
Foo.Baz = {...};

/** @typedef {{value: number}} */
Foo.Qux;

/** @interface */
Foo.Quuz = class {...}

exports.Foo = Foo;
```

这些值应当是顶层导出。为这些值选择清晰的名称（例如，对于本可以嵌套在 Foo 上的 Converter，使用 FooConverter）。不过，当模块名与类名的一部分重复时，考虑省略这种冗余：使用 `foo.Foo` 和 `foo.Converter`，而不是 `foo.Foo` 和 `foo.FooConverter`。导入方在需要清晰时可以自行添加前缀（例如 `import {Converter as FooConverter} from './foo';`），但在把整个模块作为命名空间导入时无法轻易去掉这种冗余。

```
// foo.js
goog.module('my.namespace');

class Foo {...}

class FooBar {...}

/** @enum {string} */
let FooBaz = {...};

/** @typedef {{value: number}} */
let FooQux;

/** @interface */
class FooQuuz {...};

export = {
  Foo,
  FooBar,
  FooBaz,
  FooQux,
  FooQuuz,
};
```

### 5.5 函数

#### 5.5.1 顶层函数

顶层函数可以直接定义在 `exports` 对象上，也可以在本地声明后再选择性地导出。有关导出的更多内容，参见 [3.3.3 `goog.module` 导出](#file-goog-module-exports)。

示例：

```
/** @param {string} str */
exports.processString = (str) => {
  // Process the string.
};
```

```
/** @param {string} str */
const processString = (str) => {
  // Process the string.
};

exports = {processString};
```

#### 5.5.2 嵌套函数与闭包

函数中可以包含嵌套的函数定义。如果给该函数起个名字有用，就应当把它赋给一个局部的 `const`。

#### 5.5.3 箭头函数

箭头函数提供了简洁的函数语法，并简化了嵌套函数中 `this` 的作用域。对于嵌套函数，应当优先使用箭头函数而不是 `function` 关键字（但请参见 [5.3.5 方法简写](#features-objects-method-shorthand)）。

相比其他 `this` 作用域处理方式（例如 `f.bind(this)`、`goog.bind(f, this)` 以及 `const self = this`），应当优先使用箭头函数。箭头函数在调用回调时特别有用，因为它允许显式指定传给回调的参数，而 bind 会不加区分地传入所有参数。

箭头的左侧包含零个或多个参数。如果只有一个非解构的参数，参数两侧的括号可以省略。使用括号时，可以内联指定参数类型（参见 [7.8 方法和函数注释](#jsdoc-method-and-function-comments)）。

提示：即使对于单参数的箭头函数也总是使用括号，可以避免这样一种情况：新增了参数却忘了加括号，结果代码仍能解析，但不再按预期工作。

箭头的右侧包含函数体。默认情况下，函数体是一个块语句（由花括号包围的零个或多个语句）。如果满足以下任一条件，函数体也可以是一个隐式返回的单一表达式：程序逻辑需要返回一个值，或者 `void` 运算符位于单个函数或方法调用之前（使用 `void` 可确保返回 `undefined`，防止值泄漏，并传达意图）。如果单一表达式形式能提高可读性（例如用于简短或简单的表达式），则优先使用该形式。

示例：

```
/**
 * Arrow functions can be documented just like normal functions.
 * @param {number} numParam A number to add.
 * @param {string} strParam Another number to add that happens to be a string.
 * @return {number} The sum of the two parameters.
 */
const moduleLocalFunc = (numParam, strParam) => numParam + Number(strParam);

// Uses the single expression syntax with `void` because the program logic does
// not require returning a value.
getValue((result) => void alert(`Got ${result}`));

class CallbackExample {
  constructor() {
    /** @private {number} */
    this.cachedValue_ = 0;

    // For inline callbacks, you can use inline typing for parameters.
    // Uses a block statement because the value of the single expression should
    // not be returned and the expression is not a single function call.
    getNullableValue((/** ?number */ result) => {
      this.cachedValue_ = result == null ? 0 : result;
    });
  }
}
```

不允许：

```
/**
 * A function with no params and no returned value.
 * This single expression body usage is illegal because the program logic does
 * not require returning a value and we're missing the `void` operator.
 */
const moduleLocalFunc = () => anotherFunction();
```

#### 5.5.4 生成器

生成器可以实现许多有用的抽象，可以按需使用。

定义生成器函数时，如果有 `function` 关键字，就把 `*` 附在它上面，并用一个空格把它与函数名隔开。使用委托 yield 时，把 `*` 附在 `yield` 关键字上。

示例：

```
/** @return {!Iterator<number>} */
function* gen1() {
  yield 42;
}

/** @return {!Iterator<number>} */
const gen2 = function*() {
  yield* gen1();
}

class SomeClass {
  /** @return {!Iterator<number>} */
  * gen() {
    yield 42;
  }
}
```

#### 5.5.5 参数类型与返回类型

函数参数和返回类型通常应当用 JSDoc 注解来记录。更多信息参见 [7.8 方法和函数注释](#jsdoc-method-and-function-comments)。

##### 5.5.5.1 默认参数

允许在参数列表中使用等号来声明可选参数。可选参数必须在等号两侧都带空格，命名方式与必填参数完全相同（即不加 `opt_` 前缀），在其 JSDoc 类型中使用 `=` 后缀，位于必填参数之后，并且不得使用会产生可观察副作用的初始化器。具体函数的所有可选参数都必须有默认值，即使该值是 `undefined`。与具体函数不同，抽象方法和接口方法必须省略默认参数值。

示例：

```
/**
 * @param {string} required This parameter is always needed.
 * @param {string=} optional This parameter can be omitted.
 * @param {!Node=} node Another optional parameter.
 */
function maybeDoSomething(required, optional = '', node = undefined) {}

/** @interface */
class MyInterface {
  /**
   * Interface and abstract methods must omit default parameter values.
   * @param {string=} optional
   */
  someMethod(optional) {}
}
```

谨慎使用默认参数。当有少量以上、且没有自然顺序的可选参数时，应当优先使用解构（如 [5.3.7 解构](#features-objects-destructuring) 中所述）来构建可读的 API。

注意：与 Python 的默认参数不同，使用返回新的可变对象（例如 `{}` 或 `[]`）的初始化器是没问题的，因为每次使用默认值时都会对初始化器求值，所以单个对象不会在多次调用之间共享。

提示：虽然可以使用包括函数调用在内的任意表达式作为初始化器，但这些表达式应当尽量保持简单。避免使用会暴露共享可变状态的初始化器，因为那很容易在函数调用之间引入非预期的耦合。

##### 5.5.5.2 剩余参数

使用*剩余*参数，而不要访问 `arguments`。剩余参数在其 JSDoc 中带 `...` 前缀作为类型标注。剩余参数必须是列表中的最后一个参数。`...` 与参数名之间没有空格。不要将剩余参数命名为 `var_args`。绝不要把局部变量或参数命名为 `arguments`，那会令人困惑地遮蔽内置名称。

示例：

```
/**
 * @param {!Array<string>} array This is an ordinary parameter.
 * @param {...number} numbers The remainder of arguments are all numbers.
 */
function variadic(array, ...numbers) {}
```

#### 5.5.6 泛型

必要时，在函数或方法定义上方的 JSDoc 中用 `@template TYPE` 声明泛型函数和方法。

#### 5.5.7 展开运算符

函数调用可以使用展开运算符（`...`）。当把一个数组或可迭代对象展开为一个可变参数函数的多个参数时，应当优先使用展开运算符而不是 `Function.prototype.apply`。`...` 之后没有空格。

示例：

```
function myFunction(...elements) {}
myFunction(...array, ...iterable, ...generator());
```

### 5.6 字符串字面量

#### 5.6.1 使用单引号

普通字符串字面量使用单引号（`'`）分隔，而不是双引号（`"`）。

提示：如果字符串中含有单引号字符，可以考虑使用模板字符串，以避免对引号进行转义。

普通字符串字面量不得跨多行。

#### 5.6.2 模板字符串

对于复杂的字符串拼接，尤其是涉及多个字符串字面量时，应当优先使用模板字符串（以 `` ` `` 分隔）。模板字符串可以跨多行。

如果模板字符串跨多行，它不必遵循外层块的缩进，不过如果多出的空白无关紧要，也可以遵循。

示例：

```
function arithmetic(a, b) {
  return `Here is a table of arithmetic operations:
${a} + ${b} = ${a + b}
${a} - ${b} = ${a - b}
${a} * ${b} = ${a * b}
${a} / ${b} = ${a / b}`;
}
```

#### 5.6.3 不使用续行符

不要在普通字符串字面量或模板字符串中使用*续行符*（即在字符串字面量内以反斜杠结束一行）。尽管 ES5 允许这么做，但如果反斜杠之后出现任何尾随空白，就可能导致棘手的错误，而且对读者来说也不够直观。

不允许：

```
const longString = 'This is a very long string that far exceeds the 80 \
    column limit. It unfortunately contains long stretches of spaces due \
    to how the continued lines are indented.';
```

而应当写成

```
const longString = 'This is a very long string that far exceeds the 80 ' +
    'column limit. It does not contain long stretches of spaces since ' +
    'the concatenated strings are cleaner.';
```

### 5.7 数字字面量

数字可以用十进制、十六进制、八进制或二进制表示。对于十六进制、八进制和二进制，必须分别使用完全为小写字母的 `0x`、`0o` 和 `0b` 前缀。绝不要包含前导零，除非它后面紧跟 `x`、`o` 或 `b`。

### 5.8 控制结构

#### 5.8.1 for 循环

有了 ES6，这门语言现在有三种不同的 `for` 循环。它们都可以使用，不过只要可能就应当优先使用 `for`-`of` 循环。

`for`-`in` 循环只能用于 dict 风格的对象（参见 [5.3.3 不要混用带引号和不带引号的键](#features-objects-mixing-keys)），不应用于遍历数组。在 `for`-`in` 循环中应当使用 `Object.prototype.hasOwnProperty` 来排除不需要的原型属性。只要可能，就应当优先使用 `for`-`of` 和 `Object.keys` 而不是 `for`-`in`。

#### 5.8.2 异常

异常是这门语言的重要组成部分，每当出现异常情况时就应当使用。始终抛出 `Error` 或 `Error` 的子类：绝不抛出字符串字面量或其他对象。构造 `Error` 时始终使用 `new`。

这一处理方式也适用于 `Promise` 的拒绝值，因为在 async 函数中 `Promise.reject(obj)` 等价于 `throw obj;`。

自定义异常是从函数中传递额外错误信息的绝佳方式。凡是原生 `Error` 类型不够用之处，都应当定义并使用它们。

应当优先抛出异常，而不是采用临时的错误处理方式（例如传入错误容器引用类型，或返回带错误属性的对象）。

##### 5.8.2.1 空的 catch 块

对捕获到的异常不做任何处理，极少是正确的做法。当在 catch 块中确实适合不采取任何操作时，要在注释中说明这样处理合理的理由。

```
try {
  return handleNumericResponse(response);
} catch (ok) {
  // it's not numeric; that's fine, just continue
}
return handleTextResponse(response);
```

不允许：

```
  try {
    shouldFail();
    fail('expected an error');
  } catch (expected) {
  }
```

提示：与其他一些语言不同，上面这种写法根本行不通，因为这会捕获由 `fail` 抛出的错误。而应当使用 `assertThrows()`。

#### 5.8.3 switch 语句

术语说明：switch 块的花括号内有一个或多个语句组。每个语句组由一个或多个 switch 标签（`case FOO:` 或 `default:`）以及随后的一个或多个语句组成。

##### 5.8.3.1 贯穿：需加注释

在 switch 块内，每个语句组要么突然终止（通过 `break`、`return` 或抛出的 `throw` 异常），要么标有一条注释，表明执行将继续或可能继续进入下一个语句组。任何能表达贯穿含义的注释都可以（通常是 `// fall through`）。在 switch 块的最后一个语句组中不要求写这条特殊注释。

示例：

```
switch (input) {
  case 1:
  case 2:
    prepareOneOrTwo();
  // fall through
  case 3:
    handleOneTwoOrThree();
    break;
  default:
    handleLargeNumber(input);
}
```

##### 5.8.3.2 存在 `default` 分支

每个 switch 语句都包含一个 `default` 语句组，即使它不含任何代码。`default` 语句组必须是最后一个。

### 5.9 this

只能在类构造函数和方法中、在类构造函数和方法内定义的箭头函数中，或在紧邻的外层函数的 JSDoc 中显式声明了 `@this` 的函数中使用 `this`。

绝不使用 `this` 指代全局对象、`eval` 的上下文、事件的目标，或没必要地通过 `call()` 或 `apply()` 调用的函数。

### 5.10 相等性检查

除下面记录的情况外，使用恒等运算符（`===`/`!==`）。

#### 5.10.1 适合隐式转换的例外情况

同时捕获 `null` 和 `undefined` 值：

```
if (someObjectOrPrimitive == null) {
  // Checking for null catches both null and undefined for objects and
  // primitives, but does not catch other falsy values like 0 or the empty
  // string.
}
```

### 5.11 禁用特性

#### 5.11.1 with

不要使用 `with` 关键字。它会使代码更难理解，并且自 ES5 起已在严格模式中被禁用。

#### 5.11.2 动态代码求值

不要使用 `eval` 或 `Function(...string)` 构造函数（代码加载器除外）。这些特性有潜在危险，而且在 CSP 环境中根本无法工作。

#### 5.11.3 自动分号插入

始终用分号结束语句（如上所述，函数声明和类声明除外）。

#### 5.11.4 非标准特性

不要使用非标准特性。这包括已被移除的旧特性（例如 `WeakMap.clear`）、尚未标准化的新特性（例如当前的 TC39 工作草案、任何阶段的提案，或已提议但尚未完成的 Web 标准），或只在某些浏览器中实现的专有特性。只使用当前 ECMA-262 或 WHATWG 标准中定义的特性。（注意，针对特定 API 编写的项目，例如 Chrome 扩展或 Node.js，显然可以使用这些 API）。非标准的语言「扩展」（例如某些外部转译器提供的）是禁止使用的。

#### 5.11.5 原始类型的包装对象

绝不要对原始对象包装器（`Boolean`、`Number`、`String`、`Symbol`）使用 `new`，也不要把它们用在类型注解中。

不允许：

```
const /** Boolean */ x = new Boolean(false);
if (x) alert(typeof x);  // alerts 'object' - WAT?
```

这些包装器可以当作函数调用来做隐式转换（这比使用 `+` 或拼接空字符串更可取）或创建 symbol。

示例：

```
const /** boolean */ x = Boolean(0);
if (!x) alert(typeof x);  // alerts 'boolean', as expected
```

#### 5.11.6 修改内置对象

绝不修改内置类型，无论是向其构造函数还是其原型添加方法。避免依赖这样做的库。注意，JSCompiler 的运行时库会在可能的情况下提供符合标准的 polyfill；除此之外任何东西都不得修改内置对象。

除非绝对必要（例如第三方 API 要求），否则不要向全局对象添加 symbol。

#### 5.11.7 调用构造函数时省略 `()`

在 `new` 语句中调用构造函数时，绝不省略圆括号 `()`。

不允许：

```
new Foo;
```

应当改为：

```
new Foo();
```

省略括号可能导致细微的错误。下面两行并不等价：

```
new Foo().Bar();
new Foo.Bar();
```

## 6 命名

### 6.1 适用于所有标识符的规则

标识符只使用 ASCII 字母和数字，并且在下面提到的少数情况下，还可以使用下划线，极少数情况下（当 Angular 之类框架要求时）可以使用美元符号。

在合理范围内，给出尽可能具有描述性的名称。不要担心节省水平空间，因为让新读者能立即看懂你的代码要重要得多。不要使用对你的项目之外的读者而言有歧义或不熟悉的缩写，也不要通过删除单词内部的字母来缩写。

```
errorCount          // No abbreviation.
dnsConnectionIndex  // Most people know what "DNS" stands for.
referrerUrl         // Ditto for "URL".
customerId          // "Id" is both ubiquitous and unlikely to be misunderstood.
```

不允许：

```
n                   // Meaningless.
nErr                // Ambiguous abbreviation.
nCompConns          // Ambiguous abbreviation.
wgcConnections      // Only your group knows what this stands for.
pcReader            // Lots of things can be abbreviated "pc".
cstmrId             // Deletes internal letters.
kSecondsPerDay      // Do not use Hungarian notation.
```

**例外**：作用域在 10 行以内的变量，包括*不属于*导出 API 的参数，*可以*使用简短的（例如单个字母的）变量名。

<a id="naming-rules-by-identifier-type"></a>
### 6.2 按标识符类型划分的规则

#### 6.2.1 包名

包名全部使用 `lowerCamelCase`。例如 `my.exampleCode.deepSpace`，而不是 `my.examplecode.deepspace` 或
`my.example_code.deep_space`。

**例外**：包名可以遵循 TypeScript 基于路径的模式。这一般是全小写，在文件名中出现下划线的地方使用下划线。

#### 6.2.2 类名

类、接口、record 和 typedef 的名称使用 `UpperCamelCase`。未导出的类只是局部变量：它们不标记 `@private`。

类型名通常是名词或名词短语。例如 `Request`、`ImmutableView` 或 `VisibilityMode`。此外，接口名有时也可以是形容词或形容词短语（例如 `Readable`）。

#### 6.2.3 方法名

方法名使用 `lowerCamelCase`。`@private` 方法的名称可以选择以一个结尾下划线结尾。

方法名通常是动词或动词短语。例如 `sendMessage` 或 `stop_`。属性的 getter 和 setter 方法从来不是必需的，但如果使用它们，应当命名为 `getFoo`（对于布尔值可以选择 `isFoo` 或 `hasFoo`），setter 则命名为 `setFoo(value)`。

下划线也可以出现在 JsUnit 测试方法名中，用于分隔名称的逻辑组成部分。一种典型模式是
`test<MethodUnderTest>_<state>_<expectedOutcome>`，例如
`testPop_emptyStack_throws`。测试方法的命名没有唯一正确的方式。

#### 6.2.4 枚举名

枚举名使用 `UpperCamelCase`，与类类似，并且通常应当是单数名词。枚举中的各个项使用
`CONSTANT_CASE` 命名。

#### 6.2.5 常量名

常量名使用 `CONSTANT_CASE`：全部大写字母，单词之间用下划线分隔。常量没有理由以结尾下划线命名，因为私有静态属性可以由（隐式私有的）模块局部变量替代。

##### 6.2.5.1 「常量」的定义

每个常量都是一个 `@const` 静态属性或模块局部的 `const` 声明，但并非所有 `@const` 静态属性和模块局部 `const` 都是常量。在选择常量命名格式之前，先考虑该字段是否真的像一个*深度不可变*的常量。例如，如果该实例的任何可观察状态可以改变，那它几乎肯定不是常量。仅仅打算永不修改该对象通常是不够的。

示例：

```
// Constants
const NUMBER = 5;
/** @const */ exports.NAMES = goog.debug.freeze(['Ed', 'Ann']);
/** @enum */ exports.SomeEnum = { ENUM_CONSTANT: 'value' };

// Not constants
let letVariable = 'non-const';

class MyClass {
  constructor() { /** @const {string} */ this.nonStatic = 'non-static'; }
};
/** @type {string} */
MyClass.staticButMutable = 'not @const, can be reassigned';

const /** Set<string> */ mutableCollection = new Set();

const /** MyImmutableContainer<SomeMutableType> */ stillMutable =
    new MyImmutableContainer(mutableInner);

const {Foo} = goog.require('my.foo');  // mirrors imported name

const logger = log.getLogger('loggers.are.not.immutable');
```

常量的名称通常是名词或名词短语。

##### 6.2.5.2 局部别名

当局部别名比完全限定名更具可读性时，就应当使用它。遵循与 `goog.require` 相同的规则（[3.6 `goog.require` 和 `goog.requireType` 语句](#file-goog-require)），保留被取别名名称的最后一部分。别名也可以在函数内部使用。别名必须是 `const`。

示例：

```
const staticHelper = importedNamespace.staticHelper;
const CONSTANT_NAME = ImportedClass.CONSTANT_NAME;
const {assert, assertInstanceof} = asserts;
```

#### 6.2.6 非常量字段名

非常量字段名（静态字段或其他字段）使用 `lowerCamelCase`，私有字段可以选择以一个结尾下划线结尾。

这些名称通常是名词或名词短语。例如 `computedValues` 或 `index_`。

#### 6.2.7 参数名

参数名使用 `lowerCamelCase`。注意，即使参数期望的是一个构造函数，这一点也适用。

公共方法中不应使用单字符参数名。

**例外**：当第三方框架要求时，参数名可以以 `$` 开头。此例外不适用于任何其他标识符（例如局部变量或属性）。

#### 6.2.8 局部变量名

局部变量名使用 `lowerCamelCase`，上文所述的模块局部（顶层）常量除外。函数作用域中的常量仍然使用 `lowerCamelCase` 命名。注意，即使变量保存的是一个构造函数，也使用 `lowerCamelCase`。

#### 6.2.9 模板参数名

模板参数名应当简洁，是单个单词或单个字母的标识符，并且必须全部大写，例如 `TYPE` 或 `THIS`。

#### 6.2.10 模块局部名

未导出的模块局部名隐式私有。它们不标记 `@private`。这适用于类、函数、变量、常量、枚举以及其他模块局部标识符。

### 6.3 驼峰命名：定义

有时把一个英文短语转换为驼峰命名会有不止一种合理方式，例如当出现首字母缩略词，或出现 "IPv6"、"iOS" 这类不寻常的结构时。为了提高可预测性，Google 风格规定了以下（近乎）确定性的方案。

从名称的散文形式开始：

1. 把短语转换为纯 ASCII，并移除所有撇号。例如，"Müller's algorithm" 可能变成 "Muellers algorithm"。
2. 把这个结果划分为单词，按空格和任何剩余标点（通常是连字符）拆分。
   1. 推荐做法：如果某个单词在常见用法中已经具有约定俗成的驼峰命名外观，就把它拆分为组成部分（例如 "AdWords" 变成 "ad words"）。注意，"iOS" 这样的单词本身并不是真正的驼峰命名；它不符合任何惯例，因此这一建议不适用。
3. 现在把所有内容（包括首字母缩略词）转为小写，然后只把以下内容的首字符转为大写：
   1. ……每个单词，得到 `UpperCamelCase`，或
   2. ……除第一个单词外的每个单词，得到 `lowerCamelCase`
4. 最后，把所有单词连接成单个标识符。

注意，原始单词的大小写几乎完全被忽略。

`lowerCamelCase` 的示例：

| 散文形式 | 正确 | 错误 |
| --- | --- | --- |
| "XML HTTP request" | `xmlHttpRequest` | `XMLHTTPRequest` |
| "new customer ID" | `newCustomerId` | `newCustomerID` |
| "inner stopwatch" | `innerStopwatch` | `innerStopWatch` |
| "supports IPv6 on iOS?" | `supportsIpv6OnIos` | `supportsIPv6OnIOS` |
| "YouTube importer" | `youTubeImporter` | `youtubeImporter`\* |

\*可接受，但不推荐。

有关 `UpperCamelCase` 的示例，把每个正确的 `lowerCamelCase` 示例的首字母改为大写。

注意：有些单词在英语中的连字符用法存在歧义：例如 "nonempty" 和 "non-empty" 都是正确的，因此方法名 `checkNonempty` 和 `checkNonEmpty` 同样都是正确的。

<a id="jsdoc"></a>
## 7 JSDoc

所有类、字段和方法都使用 [JSDoc](https://github.com/google/closure-compiler/wiki/Annotating-JavaScript-for-the-Closure-Compiler)。

### 7.1 总体形式

JSDoc 块的基本格式如下例所示：

```
/**
 * Multiple lines of JSDoc text are written here,
 * wrapped normally.
 * @param {number} arg A number to do something to.
 */
function doSomething(arg) { … }
```

或如这个单行示例：

```
/** @const @private {!Foo} A short bit of JSDoc. */
this.foo_ = foo;
```

如果单行注释溢出为多行，它必须使用 `/**` 和 `*/` 各自独占一行的多行风格。

许多工具会从 JSDoc 注释中提取元数据，以执行代码验证和优化。因此，这些注释**必须**格式良好。

### 7.2 Markdown

JSDoc 使用 Markdown 编写，必要时可以包含 HTML。

注意，自动提取 JSDoc 的工具（例如 [JsDossier](https://github.com/jleyba/js-dossier)）通常会忽略纯文本格式，所以如果你这样写：

```
/**
 * Computes weight based on three factors:
 *   items sent
 *   items received
 *   last timestamp
 */
```

它的输出会像这样：

```
Computes weight based on three factors: items sent items received last timestamp
```

应该改为写一个 Markdown 列表：

```
/**
 * Computes weight based on three factors:
 *
 *  - items sent
 *  - items received
 *  - last timestamp
 */
```

### 7.3 JSDoc 标签

Google 风格允许 JSDoc 标签的一个子集。完整列表见
[9.1 JSDoc 标签参考](#appendices-jsdoc-tag-reference)。大多数标签必须独占一行，标签位于行首。

不允许：

```
/**
 * The "param" tag must occupy its own line and may not be combined.
 * @param {number} left @param {number} right
 */
function add(left, right) { ... }
```

不需要任何额外数据的简单标签（例如 `@private`、
`@const`、`@final`、`@export`）可以合并到同一行，并在适当时附带一个可选的类型。

```
/**
 * Place more complex annotations (like "implements" and "template")
 * on their own lines. Multiple simple tags (like "export" and "final")
 * may be combined in one line.
 * @export @final
 * @implements {Iterable<TYPE>}
 * @template TYPE
 */
class MyClass {
  /**
   * @param {!ObjType} obj Some object.
   * @param {number=} num An optional number.
   */
  constructor(obj, num = 42) {
    /** @private @const {!Array<!ObjType|number>} */
    this.data_ = [obj, num];
  }
}
```

何时合并标签、以何种顺序合并，没有硬性规则，但要保持一致。

有关在 JavaScript 中标注类型的一般信息，参见
[Annotating JavaScript for the Closure Compiler](https://github.com/google/closure-compiler/wiki/Annotating-JavaScript-for-the-Closure-Compiler) 和
[Types in the Closure Type System](https://github.com/google/closure-compiler/wiki/Types-in-the-Closure-Type-System)。

### 7.4 换行

换行后的块标签缩进四个空格。换行后的描述文本可以与前面几行的描述对齐，但不鼓励这种水平对齐。

```
/**
 * Illustrates line wrapping for long param/return descriptions.
 * @param {string} foo This is a param with a description too long to fit in
 *     one line.
 * @return {number} This returns something that has a description too long to
 *     fit in one line.
 */
exports.method = function(foo) {
  return 5;
};
```

换行 `@desc` 或 `@fileoverview` 描述时不要缩进。

<a id="jsdoc-top-file-level-comments"></a>
### 7.5 顶层/文件级注释

文件可以有一个顶层文件概览。版权声明、作者信息和默认的[可见性级别](#jsdoc-visibility-annotations)是可选的。当一个文件包含不止一个类定义时，通常建议写文件概览。顶层注释旨在让不熟悉代码的读者了解此文件里有什么。如果存在，它可以提供文件内容的描述以及任何依赖或兼容性信息。换行不缩进。

示例：

```
/**
 * @fileoverview Description of file, its uses and information
 * about its dependencies.
 * @package
 */
```

### 7.6 类注释

类、接口和 record 必须用描述以及任何模板参数、实现的接口、可见性或其他适当的标签来记录文档。类描述应当为读者提供足够的信息，以了解如何以及何时使用该类，以及正确使用该类所需的任何其他注意事项。构造函数上的文本描述可以省略。定义类时，除非它继承一个泛型类，否则 `@constructor` 和 `@extends` 标注不与 `class` 关键字一起使用。定义 `@interface` 或 `@record` 时，定义子类时使用 `@extends` 标注，而从不使用 `extends` 关键字。

```
/**
 * A fancier event target that does cool things.
 * @implements {Iterable<string>}
 */
class MyFancyTarget extends EventTarget {
  /**
   * @param {string} arg1 An argument that makes this more interesting.
   * @param {!Array<number>} arg2 List of numbers to be processed.
   */
  constructor(arg1, arg2) {
    // ...
  }
};

/**
 * Records are also helpful.
 * @extends {Iterator<TYPE>}
 * @record
 * @template TYPE
 */
class Listable {
  /** @return {TYPE} The next item in line to be returned. */
  next() {}
}
```

### 7.7 枚举和 typedef 注释

所有枚举和 typedef 都必须在前一行用适当的 JSDoc 标签
（`@typedef` 或 `@enum`）记录文档。公共枚举和 typedef 还必须有一个描述。各个枚举项可以在前一行用 JSDoc
注释记录文档。

```
/**
 * A useful type union, which is reused often.
 * @typedef {!FruitType|!FruitTypeEnum}
 */
let CoolUnionType;
 
/**
 * Types of fruits.
 * @enum {string}
 */
const FruitTypeEnum = {
  /** This kind is very sour. */
  SOUR: 'sour',
  /** The less-sour kind. */
  SWEET: 'sweet',
};
```

Typedef 对于定义简短的 record 类型，或者联合类型、复杂函数或泛型类型的别名很有用。对于字段很多的 record 类型，应当避免使用 typedef，因为它们既不允许记录单个字段，也不能使用模板或递归引用。对于大型 record 类型，优先使用
`@record`。

<a id="jsdoc-method-and-function-comments"></a>
### 7.8 方法和函数注释

在方法和具名函数中，即使对于签名相同的 `@override`，也必须记录参数和返回类型。`this` 类型应当在必要时记录。如果函数没有非空的 `return` 语句，返回类型可以省略。

如果方法、参数和返回的描述从方法 JSDoc 的其余部分或从其签名中就能明显看出，那么这些描述（但不是类型）可以省略。

方法描述以一个描述该方法做什么的动词短语开头。这个短语不是祈使句，而是用第三人称书写，就好像前面有一个隐含的 "This method ..."。

如果方法覆盖了父类方法，它必须包含一个 `@override`
标注。对于被覆盖的方法，所有 `@param` 和 `@return` 标注都必须显式指定，即使没有细化父类方法的任何类型。这是为了与 TypeScript 保持一致。

```
/** A class that does something. */
class SomeClass extends SomeBaseClass {
  /**
   * Operates on an instance of MyClass and returns something.
   * @param {!MyClass} obj An object that for some reason needs detailed
   *     explanation that spans multiple lines.
   * @param {!OtherClass} obviousOtherClass
   * @return {boolean} Whether something occurred.
   */
  someMethod(obj, obviousOtherClass) { ... }

  /**
   * @param {string} param
   * @return {string}
   * @override
   */
  overriddenMethod(param) { ... }
}

/**
 * Demonstrates how top-level functions follow the same rules.  This one
 * makes an array.
 * @param {TYPE} arg
 * @return {!Array<TYPE>}
 * @template TYPE
 */
function makeArray(arg) { ... }
```

如果只需要记录函数的参数和返回类型，可以选择在函数签名中使用内联 JSDoc。这些内联 JSDoc 不使用标签，直接指定返回和参数类型。

```
function /** string */ foo(/** number */ arg) {...}
```

如果需要描述或标签，请使用方法上方的单个 JSDoc 注释。例如，返回值的方法需要一个 `@return` 标签。

```
class MyClass {
  /**
   * @param {number} arg
   * @return {string}
   */
  bar(arg) {...}
}
```

```
// Illegal inline JSDocs.

class MyClass {
  /** @return {string} */ foo() {...}
}

/** No function description allowed inline here. */ function bar() {...}

function /** Function description is also illegal here. */ baz() {...}
```

在匿名函数中，标注通常是可选的。如果自动类型推断不够充分，或者显式标注能提高可读性，那就像这样标注参数和返回类型：

```
promise.then(
    /** @return {string} */
    (/** !Array<string> */ items) => {
      doSomethingWith(items);
      return items[0];
    });
```

有关函数类型表达式，参见 [7.10.4 函数类型表达式](#jsdoc-function-types)。

### 7.9 属性注释

属性类型必须记录文档。对于私有属性，如果名称和类型为理解代码提供了足够的文档，那么描述可以省略。

公开导出的常量与属性的注释方式相同。

```
/** My class. */
class MyClass {
  /** @param {string=} someString */
  constructor(someString = 'default string') {
    /** @private @const {string} */
    this.someString_ = someString;

    /** @private @const {!OtherType} */
    this.someOtherThing_ = functionThatReturnsAThing();

    /**
     * Maximum number of things per pane.
     * @type {number}
     */
    this.someProperty = 4;
  }
}

/**
 * The number of times we'll try before giving up.
 * @const {number}
 */
MyClass.RETRY_COUNT = 33;
```

### 7.10 类型标注

类型标注出现在 `@param`、`@return`、`@this` 和 `@type` 标签上，
也可以选择出现在 `@const`、`@export` 和任何可见性标签上。附加到 JSDoc 标签的类型标注必须始终用花括号括起来。

#### 7.10.1 可空性

类型系统定义了修饰符 `!` 和 `?`，分别用于非空和可空。这些修饰符必须位于类型之前。

可空性修饰符对不同类型有不同的要求，分为两大类：

1. 原始类型的类型标注（`string`、`number`、`boolean`、`symbol`、
   `undefined`、`null`）和字面量（`{function(...): ...}` 和 `{{foo:
   string...}}`）默认始终不可空。使用 `?` 修饰符使其可空，但省略冗余的 `!`。
2. 引用类型（通常是指 `UpperCamelCase` 中的任何内容，包括
   `some.namespace.ReferenceType`）指代在别处定义的类、枚举、record 或 typedef。由于这些类型可能是可空的，也可能不是，仅从名称无法判断它是否可空。对这些类型始终
   使用显式的 `?` 和 `!` 修饰符，以防止在使用处产生歧义。

差：

```
const /** MyObject */ myObject = null; // Non-primitive types must be annotated.
const /** !number */ someNum = 5; // Primitives are non-nullable by default.
const /** number? */ someNullableNum = null; // ? should precede the type.
const /** !{foo: string, bar: number} */ record = ...; // Already non-nullable.
const /** MyTypeDef */ def = ...; // Not sure if MyTypeDef is nullable.

// Not sure if object (nullable), enum (non-nullable, unless otherwise
// specified), or typedef (depends on definition).
const /** SomeCamelCaseName */ n = ...;
```

好：

```
const /** ?MyObject */ myObject = null;
const /** number */ someNum = 5;
const /** ?number */ someNullableNum = null;
const /** {foo: string, bar: number} */ record = ...;
const /** !MyTypeDef */ def = ...;
const /** ?SomeCamelCaseName */ n = ...;
```

#### 7.10.2 类型转换

在编译器无法准确推断表达式类型，且
[goog.asserts](https://google.github.io/closure-library/api/goog.asserts.html)
中的断言函数无法补救的情况下，可以通过添加类型标注注释并把表达式用括号括起来，来收窄类型。注意，括号是必需的。

```
/** @type {number} */ (x)
```

#### 7.10.3 模板参数类型

始终指定模板参数。这样编译器可以做得更好，也让读者更容易理解代码做什么。

差：

```
const /** !Object */ users = {};
const /** !Array */ books = [];
const /** !Promise */ response = ...;
```

好：

```
const /** !Object<string, !User> */ users = {};
const /** !Array<string> */ books = [];
const /** !Promise<!Response> */ response = ...;

const /** !Promise<undefined> */ thisPromiseReturnsNothingButParameterIsStillUseful = ...;
const /** !Object<string, *> */ mapOfEverything = {};
```

不应使用模板参数的情况：

- `Object` 用于类型层次结构，而不是作为类似映射的结构。

<a id="jsdoc-function-types"></a>
#### 7.10.4 函数类型表达式

**术语说明**：*函数类型表达式* 指的是标注中带有关键字 `function` 的函数类型的类型标注（参见下面的示例）。

在给定了函数定义的地方，不要使用函数类型表达式。用 `@param` 和 `@return` 指定参数和返回类型，或使用内联标注（参见 [7.8 方法和函数注释](#jsdoc-method-and-function-comments)）。这包括匿名函数，以及定义并赋值给 const 的函数（此时函数 jsdoc 出现在整个赋值表达式上方）。

函数类型表达式是需要的，例如在 `@typedef`、`@param`
或 `@return` 内部。对于函数类型的变量或属性，如果没有立即用函数定义初始化，也使用它。

```
  /** @private {function(string): string} */
  this.idGenerator_ = googFunctions.identity;
```

使用函数类型表达式时，始终显式指定返回类型。否则默认返回类型是 "unknown"（`?`），这会导致奇怪和意外的行为，而且很少是实际想要的。

差 - 类型错误，但没有给出警告：

```
/** @param {function()} generateNumber */
function foo(generateNumber) {
  const /** number */ x = generateNumber();  // No compile-time type error here.
}

foo(() => 'clearly not a number');
```

好：

```
/**
 * @param {function(): *} inputFunction1 Can return any type.
 * @param {function(): undefined} inputFunction2 Definitely doesn't return
 *      anything.
 * NOTE: the return type of `foo` itself is safely implied to be {undefined}.
 */
function foo(inputFunction1, inputFunction2) {...}
```

#### 7.10.5 空白

在类型标注内部，每个逗号或冒号之后需要一个空格或换行。可以插入额外的换行以提高可读性或避免超出列限制。这些换行应当遵循适用的准则来选择并缩进（例如 [4.5 折行](#formatting-line-wrapping) 和
[4.2 块缩进：+2 个空格](#formatting-block-indentation)）。类型标注中不允许其他空白。

好：

```
/** @type {function(string): number} */

/** @type {{foo: number, bar: number}} */

/** @type {number|string} */

/** @type {!Object<string, string>} */

/** @type {function(this: Object<string, string>, number): string} */

/**
 * @type {function(
 *     !SuperDuperReallyReallyLongTypedefThatForcesTheLineBreak,
 *     !OtherVeryLongTypedef): string}
 */

/**
 * @type {!SuperDuperReallyReallyLongTypedefThatForcesTheLineBreak|
 *     !OtherVeryLongTypedef}
 */
```

差：

```
// Only put a space after the colon
/** @type {function(string) : number} */

// Put spaces after colons and commas
/** @type {{foo:number,bar:number}} */

// No space in union types
/** @type {number | string} */
```

<a id="jsdoc-visibility-annotations"></a>
### 7.11 可见性标注

可见性标注（`@private`、`@package`、`@protected`）可以在 `@fileoverview` 块中指定，或者指定在任何导出符号或属性上。不要为局部变量指定可见性，无论是在函数内还是在模块顶层。`@private` 名称可以选择以一个结尾下划线结尾。

## 8 政策

### 8.1 Google 风格未规定的问题：保持一致！

对于本规范没有给出明确结论的任何风格问题，优先采用同一文件中其他代码已经在做的做法。如果这样仍无法解决问题，可以考虑仿照同一包中的其他文件。

### 8.2 编译器警告

#### 8.2.1 使用标准警告集

项目应当尽可能使用 `--warning_level=VERBOSE`。

#### 8.2.2 如何处理警告

在做任何事之前，确保你准确理解该警告要告诉你什么。如果你不确定某个警告为什么会出现，请寻求帮助。

一旦理解了该警告，按顺序尝试以下解决方式：

1. **首先，修复它或绕过它。** 尽最大努力真正解决该警告，或找到另一种完成任务的方式，完全避开这种情况。
2. **否则，判断它是否是误报。** 如果你确信该警告无效、代码实际上是安全且正确的，就添加一条注释让读者相信这一事实，并应用 `@suppress` 标注。
3. **否则，留下 TODO 注释。** 这是**最后手段**。如果你这样做，**不得抑制该警告。** 该警告应当保持可见，直到它得到妥善处理。

#### 8.2.3 在合理的尽可能窄的范围内抑制警告

警告在合理的尽可能窄的范围内被抑制，通常是单个局部变量或极小的方法。变量或方法常常仅仅因为这个原因被提取出来。

示例

```
/** @suppress {uselessCode} Unrecognized 'use asm' declaration */
function fn() {
  'use asm';
  return 0;
}
```

即使在一个类中有大量抑制，也仍然好过让整个类对这一类警告失明。

### 8.3 废弃

用 `@deprecated` 标注标记废弃的方法、类或接口。废弃注释必须包含简单、清晰的指引，告诉人们如何修复其调用点。

### 8.4 不符合 Google 风格的代码

你偶尔会在代码库中遇到不符合规范 Google 风格的文件。它们可能来自收购，也可能是在 Google 风格对某个问题表态之前编写的，或者出于任何其他原因而属于非 Google 风格。

<a id="policies-reformatting-existing-code"></a>
#### 8.4.1 重新格式化已有代码

在更新已有代码的风格时，遵循以下准则。

1. 并不要求把所有已有代码都改成符合当前风格准则的形式。重新格式化已有代码是在代码变动量与一致性之间的权衡。风格规则会随时间演进，为保持符合性而做的这类微调会造成不必要的变动量。但是，如果要对某个文件做重大修改，则期望该文件符合 Google 风格。
2. 注意不要让顺手做的风格修复搅乱了 CL 的重点。如果你发现自己做了大量与 CL 核心重点无关的风格改动，应把这些改动提升为一个单独的 CL。

#### 8.4.2 新添加的代码：使用 Google 风格

全新的文件使用 Google 风格，无论同一包中其他文件选择了什么风格。

向不符合 Google 风格的文件添加新代码时，建议先重新格式化已有代码，但须遵循 [8.4.1 重新格式化已有代码](#policies-reformatting-existing-code) 中的建议。

如果没有做这种重新格式化，那么新代码应当尽可能与同一文件中的已有代码保持一致，但不得违反风格指南。

### 8.5 本地风格规则

团队和项目可以采用本文档之外的额外风格规则，但必须接受清理性改动可能不遵守这些额外规则，且不得因违反任何额外规则而阻止此类清理性改动。当心那些毫无意义的过度规则。风格指南并不试图为所有可能的情形定义风格，你也不应如此。

### 8.6 生成的代码：大多豁免

由构建过程生成的源代码不要求符合 Google 风格。但是，任何将被手写源代码引用的生成标识符都必须遵循命名要求。作为一项特殊例外，此类标识符允许包含下划线，这有助于避免与手写标识符冲突。

## 9 附录

<a id="appendices-jsdoc-tag-reference"></a>
### 9.1 JSDoc 标签参考

JSDoc 在 JavaScript 中有多种用途。除了用于生成文档之外，它还用于控制工具链。其中最著名的是 Closure Compiler 类型标注。

#### 9.1.1 类型标注及其他 Closure Compiler 标注

Closure Compiler 所使用的 JSDoc 文档见 [Annotating JavaScript for the Closure Compiler](https://github.com/google/closure-compiler/wiki/Annotating-JavaScript-for-the-Closure-Compiler) 和 [Types in the Closure Type System](https://github.com/google/closure-compiler/wiki/Types-in-the-Closure-Type-System)。

#### 9.1.2 文档标注

除了 [Annotating JavaScript for the Closure Compiler](https://github.com/google/closure-compiler/wiki/Annotating-JavaScript-for-the-Closure-Compiler) 中描述的 JSDoc 之外，以下标签也常见，并被各种文档生成工具（例如 [JsDossier](https://github.com/jleyba/js-dossier)）为纯粹的文档用途所良好支持。

##### 9.1.2.1 `@author` 或 `@owner` - *不推荐。*

**不推荐。**

语法：`@author username@google.com (First Last)`

```
/**
 * @fileoverview Utilities for handling textareas.
 * @author kuth@google.com (Uthur Pendragon)
 */
```

记录文件的作者或测试的所有者，通常只用在 `@fileoverview` 注释中。`@owner` 标签被单元测试仪表盘用来确定谁拥有测试结果。

##### 9.1.2.2 `@bug`

语法：`@bug bugnumber`

```
/** @bug 1234567 */
function testSomething() {
  // …
}

/**
 * @bug 1234568
 * @bug 1234569
 */
function testTwoBugs() {
  // …
}
```

指明给定测试函数针对哪些 bug 做回归测试。

多个 bug 应当各自单独占一行 `@bug`，以便尽可能方便地搜索回归测试。

##### 9.1.2.3 `@code` - *废弃。不得使用。*

**废弃。不得使用。请改用 Markdown 反引号。**

语法：`{@code ...}`

历史上，`` `BatchItem` `` 曾被写作 `{@code BatchItem}`。

```
/** Processes pending `BatchItem` instances. */
function processBatchItems() {}
```

指明 JSDoc 描述中的某个词是代码，以便它能在生成的文档中被正确格式化。

##### 9.1.2.4 `@desc`

语法：`@desc Message description`

```
/** @desc Notifying a user that their account has been created. */
exports.MSG_ACCOUNT_CREATED = goog.getMsg(
    'Your account has been successfully created.');
```

##### 9.1.2.5 `@link`

语法：`{@link ...}`

该标签用于在生成的文档中生成交叉引用链接。

```
/** Processes pending {@link BatchItem} instances. */
function processBatchItems() {}
```

**历史说明：** @link 标签也曾被用来在生成的文档中创建外部链接。对于外部链接，请始终改用 Markdown 的链接语法：

```
/**
 * This class implements a useful subset of the
 * [native Event interface](https://dom.spec.whatwg.org/#event).
 */
class ApplicationEvent {}
```

##### 9.1.2.6 `@see`

语法：`@see Link`

```
/**
 * Adds a single item, recklessly.
 * @see #addSafely
 * @see goog.Collect
 * @see goog.RecklessAdder#add
 */
```

引用指向另一个类函数或方法的查找。

##### 9.1.2.7 `@supported`

语法：`@supported Description`

```
/**
 * @fileoverview Event Manager
 * Provides an abstracted interface to the browsers' event systems.
 * @supported IE10+, Chrome, Safari
 */
```

用在 fileoverview 中，指明该文件支持哪些浏览器。

你还可能在第三方代码中看到其他类型的 JSDoc 标注。这些标注出现在 [JSDoc Toolkit Tag Reference](http://code.google.com/p/jsdoc-toolkit/wiki/TagReference) 中，但不被视为有效 Google 风格的一部分。

#### 9.1.3 框架特定的标注

以下标注特定于某个特定框架。

##### 9.1.3.1 用于 Angular 1 的 `@ngInject`

##### 9.1.3.2 用于 Polymer 的 `@polymerBehavior`

<https://github.com/google/closure-compiler/wiki/Polymer-Pass>

#### 9.1.4 关于标准 Closure Compiler 标注的说明

以下标签曾经是标准的，但现在已废弃。

##### 9.1.4.1 `@expose` - *废弃。不得使用。*

**废弃。不得使用。请改用 `@export` 和/或 `@nocollapse`。**

##### 9.1.4.2 `@inheritDoc` - *废弃。不得使用。*

**废弃。不得使用。请改用 `@override`。**

### 9.2 常被误解的风格规则

以下汇集了关于 Google JavaScript 风格的较少人知或常被误解的事实。（以下都是真实陈述；这不是一份「迷思」清单。）

- 源文件中既不要求版权声明，也不要求 `@author` 署名。（两者也都不是明确推荐的。）
- 没有关于如何排列类的成员的「硬性」规则（[5.4 类](#features-classes)）。
- 空块通常可以简洁地表示为 `{}`，详见（[4.1.3 空块：可以简洁](#formatting-empty-blocks)）。
- 换行的首要原则是：优先在更高的语法层级处断开（[4.5.1 在哪里断行](#formatting-where-to-break)）。
- 字符串字面量、注释和 JSDoc 中允许使用非 ASCII 字符，事实上，当非 ASCII 字符比等效的 Unicode 转义更易读时，推荐使用它们（[2.3.3 非 ASCII 字符](#non-ascii-characters)）。

### 9.3 与风格相关的工具

以下工具用于支持 Google 风格的各个方面。

#### 9.3.1 Closure Compiler

该程序执行类型检查和其他检查、优化和其他转换（例如把代码降级到 ECMAScript 5）。

#### 9.3.2 `clang-format`

该程序把 JavaScript 源代码重新格式化为 Google 风格，并且还遵循若干非必需但常常能提升可读性的格式化实践。`clang-format` 产生的输出符合风格指南。

`clang-format` 不是必需的。作者可以修改它的输出，审查者也可以要求此类修改；争议按通常方式解决。但是，各个子树可以在本地选择启用这种强制执行。

#### 9.3.3 Closure 编译器静态检查工具（linter）

该程序检查各种失误和反模式。

#### 9.3.4 符合性框架

JS Conformance Framework 是 Closure Compiler 的一部分，它为开发者提供了一种简单的方式，用于在其代码库上指定一组在标准检查之上额外运行的检查。例如，符合性检查可以禁止访问某个属性、禁止调用某个函数，或禁止缺失类型信息（unknowns）。

这些规则通常用于强制实施关键限制（例如定义全局变量，这可能破坏代码库）和安全模式（例如使用 `eval` 或给 `innerHTML` 赋值），或者更宽松地用于提高代码质量。

更多信息见 [JS Conformance Framework](https://github.com/google/closure-compiler/wiki/JS-Conformance-Framework) 的官方文档。

### 9.4 旧式平台的例外

#### 9.4.1 概述

本节描述当代码作者无法使用现代 ECMAScript 语法时要遵循的例外和附加规则。当无法使用现代 ECMAScript 语法时，就要求对推荐风格作例外处理，这些例外在此概述：

- 允许使用 `var` 声明
- 允许使用 `arguments`
- 允许使用不带默认值的可选参数

#### 9.4.2 使用 `var`

##### 9.4.2.1 `var` 声明不是块级作用域

`var` 声明的作用域从最近的封闭函数、脚本或模块的开头开始，这可能导致意外行为，尤其是引用了循环内部 `var` 声明的函数闭包。以下代码给出了一个示例：

```
for (var i = 0; i < 3; ++i) {
  var iteration = i;
  setTimeout(function() { console.log(iteration); }, i*1000);
}

// logs 2, 2, 2 -- NOT 0, 1, 2
// because `iteration` is function-scoped, not local to the loop.
```

##### 9.4.2.2 尽可能靠近首次使用处声明变量

尽管 `var` 声明的作用域从封闭函数的开头开始，但出于可读性考虑，`var` 声明应当尽可能靠近其首次使用处。但是，如果某个变量会在块外被引用，就不要把该 `var` 声明放在块内。例如：

```
function sillyFunction() {
  var count = 0;
  for (var x in y) {
    // "count" could be declared here, but don't do that.
    count++;
  }
  console.log(count + ' items in y');
}
```

##### 9.4.2.3 对常量变量使用 @const

对于在 `const` 关键字可用时会使用该关键字的全局声明，改为用 `@const` 标注该 `var` 声明（对局部变量而言这是可选的）。

#### 9.4.3 不要使用块级函数声明

**不要**这样做：

```
if (x) {
  function foo() {}
}
```

虽然在 ECMAScript 6 之前实现的大多数 JavaScript VM 都支持块内的函数声明，但这并未被标准化。各实现之间互不一致，也与如今标准化的 ECMAScript 块级函数声明行为不一致。ECMAScript 5 标准及更早的标准只允许在脚本或函数的根语句列表中声明函数，并且在严格模式下明确禁止在块作用域中声明函数。

为获得一致的行为，改为使用以函数表达式初始化的 `var` 来在块内定义函数：

```
if (x) {
  var foo = function() {};
}
```

#### 9.4.4 使用 `goog.provide`/`goog.require` 进行依赖管理

##### 9.4.4.1 概要

**警告：`goog.provide` 依赖管理已废弃。** 所有新文件，即使在为较旧文件使用 `goog.provide` 的项目中，也应当使用 [`goog.module`](#source-file-structure)。以下规则仅适用于已有的 `goog.provide` 文件。

- 把所有 `goog.provide` 放在最前，`goog.require` 放在其后。用空行分隔 provides 与 requires。
- 按字母顺序排序条目（大写在前）。
- 不要折行 `goog.provide` 和 `goog.require` 语句。必要时可以超过 80 列。
- 只 provide 顶层符号。

`goog.provide` 语句应当归在一起并放在最前。所有 `goog.require` 语句应当紧随其后。两个列表之间应当用空行分隔。

与其他语言中的 import 语句类似，`goog.provide` 和 `goog.require` 语句应当写在一行内，即使它们超过了 80 列的行长限制。

各行应当按字母顺序排序，大写字母在前：

```
goog.provide('namespace.MyClass');
goog.provide('namespace.helperFoo');

goog.require('an.extremelyLongNamespace.thatSomeoneThought.wouldBeNice.andNowItIsLonger.Than80Columns');
goog.require('goog.dom');
goog.require('goog.dom.TagName');
goog.require('goog.dom.classes');
goog.require('goog.dominoes');
```

定义在同一个类上的所有成员都应当在同一个文件中。在包含定义于同一个类上的多个成员（例如枚举、内部类等）的文件中，只应 provide 顶层类。

这样做：

```
goog.provide('namespace.MyClass');
```

不要这样做：

```
goog.provide('namespace.MyClass');
goog.provide('namespace.MyClass.CONSTANT');
goog.provide('namespace.MyClass.Enum');
goog.provide('namespace.MyClass.InnerClass');
goog.provide('namespace.MyClass.TypeDef');
goog.provide('namespace.MyClass.staticMethod');
```

命名空间上的成员也可以被 provide：

```
goog.provide('foo.bar');
goog.provide('foo.bar.CONSTANT');
goog.provide('foo.bar.method');
```

##### 9.4.4.2 使用 `goog.scope` 创建别名

**警告：`goog.scope` 已废弃。** 新文件不应使用 `goog.scope`，即使在已有 `goog.scope` 用法的项目中也是如此。

`goog.scope` 可用于缩短使用了 `goog.provide`/`goog.require` 依赖管理的代码中对命名空间符号的引用。

每个文件只能添加一次 `goog.scope` 调用。始终把它放在全局作用域中。

起始的 `goog.scope(function() {` 调用之前必须恰好有一个空行，并且位于所有 `goog.provide` 语句、`goog.require` 语句或顶层注释之后。该调用必须在文件的最后一行闭合。在作用域的结束语句后追加 `// goog.scope`。用两个空格分隔注释与分号。

与 C++ 命名空间类似，不要在 `goog.scope` 声明之下缩进。而应从第 0 列继续。

只为不会被重新赋值为另一个对象的名字创建别名（例如大多数构造函数、枚举和命名空间）。不要这样做（如何为构造函数创建别名见下文）：

```
goog.scope(function() {
var Button = goog.ui.Button;

Button = function() { ... };
...
```

别名必须与其所指向的全局对象最后一个属性同名。

```
goog.provide('my.module.SomeType');

goog.require('goog.dom');
goog.require('goog.ui.Button');

goog.scope(function() {
var Button = goog.ui.Button;
var dom = goog.dom;

// Alias new types after the constructor declaration.
my.module.SomeType = function() { ... };
var SomeType = my.module.SomeType;

// Declare methods on the prototype as usual:
SomeType.prototype.findButton = function() {
  // Button as aliased above.
  this.button = new Button(dom.getElement('my-button'));
};
...
});  // goog.scope
```

##### 9.4.4.3 `goog.forwardDeclare`

在同一库中的文件之间打破循环依赖时，优先使用 `goog.requireType` 而不是 `goog.forwardDeclare`。与 `goog.require` 不同，`goog.requireType` 语句允许在命名空间定义之前导入它。

`goog.forwardDeclare` 语句必须遵循与 `goog.require` 和 `goog.requireType` 相同的风格规则。`goog.forwardDeclare`、`goog.require` 和 `goog.requireType` 语句的整个块按字母顺序排序。

`goog.forwardDeclare` 在遗留代码中用于打破*跨库边界*的循环引用。但是这种模式对构建工具的支持很差，不应使用。应当通过组织代码来避免跨库的循环依赖（通过拆分/合并库）。

##### 9.4.4.4 `goog.module.get(name)`

如果一个 `goog.provide` 文件依赖于一个 `goog.module` 文件，该 `goog.provide` 文件通常无法通过全局名字引用该模块的导出。此时，除了对模块执行 `goog.require()` 之外，`goog.provide` 文件还必须通过调用 `goog.module.get('module.name')` 来获取模块的导出对象。

注意：只调用 `goog.module.get('module.name')` 不会在你的代码与该模块之间创建构建期依赖。构建依赖需要 `goog.require`。

##### 9.4.4.5 `goog.module.declareLegacyNamespace()`

**警告：`goog.module.declareLegacyNamespace` 仅用于过渡期使用。**

`goog.module.declareLegacyNamespace` 仅在把一个 JavaScript 文件及其使用方从 `goog.provide` 迁移到 `goog.module` 的过程中使用。请更新你的 [`goog.module`](#source-file-structure) 的使用方，让它们自己也使用 `goog.module`。尽可能移除对 `goog.module.declareLegacyNamespace` 的调用。

如果你无法很快把遗留命名空间的使用方从 `goog.provide` 更新到 `goog.module`，请把你文件的内容包裹在对 `goog.scope` 的调用中，用 `goog.module.get` 导入该遗留命名空间，然后删除你 `goog.module` 中对 `goog.module.declareLegacyNamespace` 的调用。

在 `goog.module(name)` 内调用 `goog.module.declareLegacyNamespace()` 会把该模块的命名空间声明为一个全局名字，就像 `goog.provide()` 调用所做的那样。这允许非 `goog.module` 命名空间在不调用 `goog.module.get(name)` 的情况下访问该模块的导出。
