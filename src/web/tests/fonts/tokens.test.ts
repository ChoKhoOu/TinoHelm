import { describe, it, expect } from 'vitest';
import {
  readGlobalsCss,
  getRootDecls,
  getBodyRule,
  getThemeInlineDecls,
  globalCssText,
} from './fixtures/parse-css';

describe('QDS Font Tokens (globals.css)', () => {
  const css = readGlobalsCss();
  const root = getRootDecls(css);
  const body = getBodyRule(css);
  const themeInline = getThemeInlineDecls(css);

  it('(sanity) body rule contains @apply directive', () => {
    expect(body.hasApply).toBe(true);
  });

  it('--font-sans leads with var(--font-inter)', () => {
    const v = root.get('--font-sans') ?? '';
    expect(v).toMatch(/^var\(--font-inter\)/);
  });

  it('--font-mono leads with var(--font-jetbrains-mono)', () => {
    const v = root.get('--font-mono') ?? '';
    expect(v).toMatch(/^var\(--font-jetbrains-mono\)/);
  });

  it('Sans fallback chain contains PingFang SC (word-boundary)', () => {
    expect(root.get('--font-sans')).toMatch(/\bPingFang\s+SC\b/);
  });

  it('Mono fallback chain contains Sarasa Mono SC (word-boundary)', () => {
    expect(root.get('--font-mono')).toMatch(/\bSarasa\s+Mono\s+SC\b/);
  });

  it('Legacy --font-d aliases to var(--font-mono)', () => {
    expect(root.get('--font-d')?.trim()).toBe('var(--font-mono)');
  });

  it('Legacy --font-u aliases to var(--font-sans)', () => {
    expect(root.get('--font-u')?.trim()).toBe('var(--font-sans)');
  });

  it('body declares font-feature-settings cv11', () => {
    expect(body.decls.get('font-feature-settings')).toMatch(/cv11/);
  });
  it('body declares font-feature-settings ss01', () => {
    expect(body.decls.get('font-feature-settings')).toMatch(/ss01/);
  });
  it('body declares font-feature-settings ss03', () => {
    expect(body.decls.get('font-feature-settings')).toMatch(/ss03/);
  });

  it('@theme inline --font-sans resolves to --font-sans or --font-inter', () => {
    expect(themeInline.get('--font-sans')).toMatch(/var\(--font-(sans|inter)\)/);
  });

  it('@theme inline --font-mono resolves to --font-mono or --font-jetbrains-mono', () => {
    expect(themeInline.get('--font-mono')).toMatch(/var\(--font-(mono|jetbrains-mono)\)/);
  });

  it('No legacy .font-sans/.font-mono/.font-heading class overrides bound to --font-u/--font-d', () => {
    expect(globalCssText()).not.toMatch(
      /\.font-(sans|mono|heading)\s*\{[^}]*var\(--font-[du]\)/,
    );
  });

  it('No IBM Plex literal remains in globals.css', () => {
    expect(globalCssText()).not.toMatch(/IBM Plex/);
  });

  it('--font-sans / --font-mono definitions are not commented out', () => {
    const text = globalCssText();
    expect(text).not.toMatch(/\/\*[^*]*--font-sans\s*:/);
    expect(text).not.toMatch(/\/\*[^*]*--font-mono\s*:/);
  });
});
