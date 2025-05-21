/* ESLint 8 (classic config) - matches the pinned eslint ^8.57 in package.json. */
module.exports = {
  root: true,
  env: { browser: true, es2022: true, node: true },
  extends: [
    'eslint:recommended',
    'plugin:@typescript-eslint/recommended',
  ],
  parser: '@typescript-eslint/parser',
  parserOptions: { ecmaVersion: 'latest', sourceType: 'module' },
  plugins: ['@typescript-eslint', 'react-hooks'],
  ignorePatterns: ['dist', 'node_modules', 'coverage', 'playwright-report', 'test-results', '*.cjs'],
  rules: {
    'react-hooks/rules-of-hooks': 'error',
    'react-hooks/exhaustive-deps': 'warn',
    // Unused variables are an error, but an underscore prefix marks a
    // deliberate discard (e.g. an unused catch binding or ignored arg).
    '@typescript-eslint/no-unused-vars': ['error', { argsIgnorePattern: '^_', varsIgnorePattern: '^_' }],
    // The API boundary is typed; `any` is a smell we want flagged, not fatal.
    '@typescript-eslint/no-explicit-any': 'warn',
    'no-console': ['warn', { allow: ['warn', 'error'] }],
    eqeqeq: ['error', 'smart'],
    'prefer-const': 'error',
  },
  overrides: [
    {
      files: ['e2e/**/*.ts', 'src/**/*.test.{ts,tsx}', 'src/test/**/*.ts'],
      env: { node: true },
      rules: { 'no-console': 'off' },
    },
  ],
};
