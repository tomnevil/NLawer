// §3.3 / §3.2 取证：`cn()`（clsx + tailwind-merge）会悄悄吃掉本项目的**语义字号类**。
//
// 为什么这是「技术栈级」的：tailwind-merge 靠**值的形状**猜 `text-<x>` 是字号还是颜色。
// Tailwind 的**标准**档（text-xs/sm/base/lg/xl…）它认识；本项目的**语义**档
// （text-caption/label/body-sm/body/body-lg/h4…）它不认识 ⇒ 归到「文字颜色」组
// ⇒ 与后面的 `text-<颜色>` 同组冲突 ⇒ **后者赢，前者被丢**。
//
// 用法：
//   node evidence/typography_twmerge_probe.cjs
//
// ⚠️ Node 按**脚本所在目录**向上找 node_modules，不按 cwd。
// `tailwind-merge` 装在 `frontend/node_modules` ⇒ 必须显式指路，
// 否则报 `Cannot find module 'tailwind-merge'`（实测踩到）。
const path = require('path');
const { twMerge } = require(
  path.join(__dirname, '..', 'frontend', 'node_modules', 'tailwind-merge'));

const show = (label, ...args) => {
  const out = twMerge(...args);
  const probe = (tok) => (out.includes(tok) ? '存活' : '**被吃掉**');
  console.log(`\n[${label}]`);
  console.log(`  in  = ${JSON.stringify(args)}`);
  console.log(`  out = ${out}`);
  return { out, probe };
};

console.log('=== 一、真实调用点（取自源码，逐条可核） ===');

// Card.tsx:66  CardTitle
let r = show('Card.tsx:66 CardTitle（text-h4 在前）',
  'text-h4 font-semibold text-ink-900', 'mt-2');
console.log(`  text-h4        → ${r.probe('text-h4')}`);
console.log(`  font-semibold  → ${r.probe('font-semibold')}`);

// Card.tsx:76  CardDescription
r = show('Card.tsx:76 CardDescription（text-body-sm 在前）',
  'mt-1 text-body-sm text-ink-500', 'mb-3');
console.log(`  text-body-sm   → ${r.probe('text-body-sm')}`);

// Table.tsx:88  <th>
r = show('Table.tsx:88 <th>（text-label 在前）',
  'border-b border-line px-4 py-2.5 text-label font-medium', 'text-ink-500',
  'text-center');
console.log(`  text-label     → ${r.probe('text-label')}`);
console.log(`  font-medium    → ${r.probe('font-medium')}`);

// Table.tsx:116 <td>
r = show('Table.tsx:116 <td>（text-body-sm 与颜色在**同一字面量**内）',
  'px-4 py-2.5 text-body-sm text-ink-700', 'text-center');
console.log(`  text-body-sm   → ${r.probe('text-body-sm')}`);

// Timeline.tsx:41
r = show('Timeline.tsx:41（同上，单字面量内）',
  'py-6 text-center text-body-sm text-ink-500', undefined);
console.log(`  text-body-sm   → ${r.probe('text-body-sm')}`);

console.log('\n=== 二、对照组：Tailwind 标准字号档**不受影响** ===');
for (const s of ['text-xs', 'text-sm', 'text-base', 'text-lg']) {
  const out = twMerge(`${s} text-ink-700`);
  console.log(`  ${s.padEnd(10)} + text-ink-700 → ${out}   （${out.includes(s) ? '存活' : '被吃'}）`);
}

console.log('\n=== 三、本项目 8 个语义字号档**全部**受影响 ===');
for (const s of ['text-caption', 'text-label', 'text-body-sm', 'text-body',
                 'text-body-lg', 'text-h4', 'text-h3', 'text-h2']) {
  const out = twMerge(`${s} text-ink-700`);
  console.log(`  ${s.padEnd(14)} + text-ink-700 → ${out}   （${out.includes(s) ? '存活' : '**被吃**'}）`);
}

console.log('\n=== 四、顺序决定谁被吃（同一对类，两种顺序） ===');
const A = twMerge('text-body-sm text-white');
const B = twMerge('text-white text-body-sm');
console.log(`  text-body-sm 在前 → ${A}   （text-body-sm ${A.includes('text-body-sm') ? '存活' : '被吃'}, text-white ${A.includes('text-white') ? '存活' : '被吃'}）`);
console.log(`  text-white   在前 → ${B}   （text-body-sm ${B.includes('text-body-sm') ? '存活' : '被吃'}, text-white ${B.includes('text-white') ? '存活' : '被吃'}）`);
console.log('  ⇒ 是「**后写的赢**」的组内覆盖，不是「总是丢字号」。');

console.log('\n=== 五、非字号/颜色组不受影响（对照） ===');
for (const [a, b] of [['text-body-sm', 'font-medium'], ['text-body-sm', 'bg-brand-600'],
                      ['text-body-sm', 'leading-relaxed'], ['text-body-sm', 'text-center']]) {
  const out = twMerge(a, b);
  console.log(`  ${a.padEnd(14)} + ${b.padEnd(18)} → ${out}   （${out.includes(a) ? '存活' : '**被吃**'}）`);
}

console.log('\n=== 六、同一字面量内部也会发生（无需多参数） ===');
const C = twMerge('px-4 text-body-sm text-ink-700');
console.log(`  'px-4 text-body-sm text-ink-700' → ${C}   （text-body-sm ${C.includes('text-body-sm') ? '存活' : '**被吃**'}）`);
console.log('  ⇒ 只要该字面量**经过 cn()**，同一串里的字号也会被吃掉。');
