// The two addresses every test file uses (16/11/2026). One Playwright group for
// all tests, so the interface shows every test at once; each file says which
// site it tests with test.use({ baseURL }).
export const SITE_URL = (process.env.SITE_URL || 'http://localhost:5176').replace(/\/$/, '');
export const ADMIN_URL = (process.env.ADMIN_URL || 'http://localhost:5177').replace(/\/$/, '');
