/**
 * Design tokens — the single source of truth for the visual system.
 *
 * SPEC.md § Frontend § Design system fixes these values. Components reference
 * tokens only: no inline hex (`bg-[#123456]`), no arbitrary spacing (`p-[23px]`),
 * no arbitrary type sizes (`text-[14.5px]`). If something needs a value that is
 * not here, it gets added here first.
 */
import type { Config } from 'tailwindcss'
import animate from 'tailwindcss-animate'

export default {
  // No `darkMode` toggle: the app is single-theme dark. The palette below IS
  // the theme, applied at the root, so there is no light variant to switch to.
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        bg: '#0B0D0F', // warm near-black — root background
        surface: '#14171A', // elevated cards, panels
        'surface-hover': '#1B1F23',
        border: '#252A2F',
        text: {
          DEFAULT: '#F3F4F6',
          secondary: '#9CA3AF',
          muted: '#6B7280',
        },
        accent: '#F97316', // vibrant orange — primary brand + interactive
        'accent-hover': '#EA580C',
        ok: '#10B981', // emerald — operating normally
        warn: '#F59E0B', // amber — approaching threshold
        alert: '#EF4444', // red — threshold exceeded
      },
      fontFamily: {
        // Inter for UI, JetBrains Mono for anything numeric: values, device ids,
        // timestamps. Tabular figures stop numbers jittering as they update.
        sans: ['Inter Variable', 'Inter', 'system-ui', 'sans-serif'],
        mono: ['JetBrains Mono', 'ui-monospace', 'monospace'],
      },
      fontSize: {
        // The five sizes SPEC allows, named by role rather than by pixel count.
        cell: ['0.75rem', { lineHeight: '1rem' }], // 12px — table cells
        chrome: ['0.875rem', { lineHeight: '1.25rem' }], // 14px — UI chrome
        body: ['1rem', { lineHeight: '1.5rem' }], // 16px — body copy
        'card-title': ['1.25rem', { lineHeight: '1.75rem' }], // 20px
        'page-title': ['1.75rem', { lineHeight: '2.25rem' }], // 28px
      },
      borderRadius: {
        // shadcn primitives read from this variable; 6px is the SPEC radius.
        md: '0.375rem',
      },
      transitionTimingFunction: {
        // SPEC motion rule: 150ms ease-out on interactive transitions.
        DEFAULT: 'cubic-bezier(0, 0, 0.2, 1)',
      },
      transitionDuration: {
        DEFAULT: '150ms',
      },
    },
  },
  plugins: [animate],
} satisfies Config
