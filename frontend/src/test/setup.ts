import '@testing-library/jest-dom/vitest'

// jsdom implements no layout engine, so `Element.prototype.scrollIntoView` is
// simply absent — calling it throws rather than doing nothing. The gap is in the
// environment, not in the components that use it, so it is filled here instead of
// being guarded around every call site.
// Checked with `typeof` rather than `in`: an `in` guard against a type that
// already declares the member narrows the branch to `never`, so the assignment
// inside it will not type-check.
if (typeof Element.prototype.scrollIntoView !== 'function') {
  Element.prototype.scrollIntoView = (): void => {}
}
