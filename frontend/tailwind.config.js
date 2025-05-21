/** @type {import('tailwindcss').Config} */
export default {
  darkMode: 'class',
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        brand: {
          50: '#eef5ff', 100: '#d9e8ff', 200: '#bcd7ff', 300: '#8ebeff',
          400: '#599bff', 500: '#3377f6', 600: '#1f58db', 700: '#1a46b0',
          800: '#1b3c8b', 900: '#1b366e',
        },
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'Segoe UI', 'Roboto', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
    },
  },
  plugins: [],
};
