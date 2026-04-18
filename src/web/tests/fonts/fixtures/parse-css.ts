import { readFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import postcss, { Rule, AtRule } from 'postcss';

const __dirname = dirname(fileURLToPath(import.meta.url));
const CSS_PATH = resolve(__dirname, '../../../src/app/globals.css');

export function readGlobalsCss(): string {
  return readFileSync(CSS_PATH, 'utf-8');
}

export function getRootDecls(css: string): Map<string, string> {
  const root = postcss.parse(css);
  const decls = new Map<string, string>();
  root.walkRules((rule) => {
    if (rule.selector.trim() === ':root') {
      rule.walkDecls((d) => {
        decls.set(d.prop, d.value);
      });
      return false;
    }
  });
  return decls;
}

export interface BodyRuleSnapshot {
  decls: Map<string, string>;
  raw: string;
  hasApply: boolean;
}

export function getBodyRule(css: string): BodyRuleSnapshot {
  const root = postcss.parse(css);
  const matches: Rule[] = [];
  root.walkRules((rule) => {
    if (rule.selector.trim() === 'body') {
      matches.push(rule);
      return false;
    }
  });
  const target = matches[0];
  if (target === undefined) {
    throw new Error('body { } rule not found (exact selector)');
  }
  const decls = new Map<string, string>();
  target.walkDecls((d) => {
    decls.set(d.prop, d.value);
  });
  const hasApply = target.nodes.some(
    (n) => n.type === 'atrule' && (n as AtRule).name === 'apply',
  );
  return { decls, raw: target.toString(), hasApply };
}

export function getThemeInlineDecls(css: string): Map<string, string> {
  const root = postcss.parse(css);
  const decls = new Map<string, string>();
  root.walkAtRules('theme', (atRule) => {
    if (atRule.params.trim() === 'inline') {
      atRule.walkDecls((d) => {
        decls.set(d.prop, d.value);
      });
      return false;
    }
  });
  return decls;
}

export function globalCssText(): string {
  return readGlobalsCss();
}
