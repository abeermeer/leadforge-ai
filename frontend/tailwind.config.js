/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        // Matched to trax9.com — indigo primary, cyan + orange accents.
        // Token names kept (gold/cyan/violet) so existing classes cascade;
        // "gold" now carries the Trax9 orange CTA colour.
        trax9: {
          bg: '#eef2fb', // Trax9 pale lavender-white (site body #F0F5FC)
          panel: '#ffffff', // white cards
          'panel-solid': '#f6f8fe',
          border: '#e2e7f4', // light indigo-grey hairline
          gold: '#4914c4', // Trax9 primary — vivid violet (site headings)
          cyan: '#05c3de', // Trax9 exact cyan (live/signal)
          green: '#10b981',
          red: '#ef4444',
          violet: '#7c3aed', // secondary violet (AI/brand moments)
          blue: '#007bff', // Trax9 link blue
          orange: '#ff8a00', // Trax9 accent orange
          text: '#2a2550', // deep indigo body text
          muted: '#6f6b96', // muted indigo (site #504C89 family)
          ink: '#ffffff', // text on violet/gold buttons
        },
      },
      fontFamily: {
        sans: ['"Open Sans"', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        display: ['"Josefin Sans"', '"Open Sans"', 'ui-sans-serif', 'sans-serif'],
        mono: ['ui-monospace', '"Cascadia Code"', 'Menlo', 'Consolas', 'monospace'],
      },
      boxShadow: {
        'glow-gold': '0 4px 14px -2px rgba(73,20,196,0.35)',
        'glow-cyan': '0 0 0 1px rgba(5,195,222,0.4), 0 0 14px 1px rgba(5,195,222,0.28)',
        card: '0 1px 3px 0 rgba(42,37,80,0.08), 0 1px 2px -1px rgba(42,37,80,0.06)',
      },
      keyframes: {
        'pulse-glow': {
          '0%, 100%': { boxShadow: '0 0 0 0 rgba(73,20,196,0.3)' },
          '50%': { boxShadow: '0 0 16px 4px rgba(73,20,196,0.16)' },
        },
        flow: {
          to: { strokeDashoffset: '-24' },
        },
        scan: {
          '0%': { transform: 'translateX(-100%)' },
          '100%': { transform: 'translateX(400%)' },
        },
        'fade-up': {
          from: { opacity: '0', transform: 'translateY(8px)' },
          to: { opacity: '1', transform: 'translateY(0)' },
        },
        // data pulse travelling along a connector into the active agent
        travel: {
          '0%': { transform: 'translateX(0)', opacity: '0' },
          '15%': { opacity: '1' },
          '85%': { opacity: '1' },
          '100%': { transform: 'translateX(var(--travel, 80px))', opacity: '0' },
        },
        // active agent "breathing" halo
        breathe: {
          '0%, 100%': { transform: 'scale(1)', opacity: '0.55' },
          '50%': { transform: 'scale(1.35)', opacity: '0' },
        },
        // slow orbit ring around a working agent
        orbit: {
          from: { transform: 'rotate(0deg)' },
          to: { transform: 'rotate(360deg)' },
        },
        // count badge tick
        tick: {
          '0%, 100%': { transform: 'scale(1)' },
          '50%': { transform: 'scale(1.18)' },
        },
        // ambient backdrop drift
        drift: {
          '0%, 100%': { transform: 'translate3d(0,0,0)' },
          '50%': { transform: 'translate3d(2%, 1.5%, 0)' },
        },
        blink: {
          '0%, 100%': { opacity: '1' },
          '50%': { opacity: '0.25' },
        },
      },
      animation: {
        'pulse-glow': 'pulse-glow 2.2s ease-in-out infinite',
        flow: 'flow 1.2s linear infinite',
        scan: 'scan 2.8s ease-in-out infinite',
        'fade-up': 'fade-up 0.4s ease-out both',
        travel: 'travel 1.4s ease-in-out infinite',
        breathe: 'breathe 2.4s ease-out infinite',
        orbit: 'orbit 6s linear infinite',
        tick: 'tick 1.6s ease-in-out infinite',
        drift: 'drift 24s ease-in-out infinite',
        blink: 'blink 1.4s ease-in-out infinite',
      },
    },
  },
  plugins: [],
};
