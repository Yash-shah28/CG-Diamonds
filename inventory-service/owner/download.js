const login = document.querySelector('#login');
const change = document.querySelector('#change');
const password = document.querySelector('#password');
const status = document.querySelector('#status');
const download = document.querySelector('#download');
let token;

async function request(path, body) {
  const headers = token ? { Authorization: 'Bearer ' + token } : {};
  if (body) headers['Content-Type'] = 'application/json';
  const response = await fetch(path, {
    method: body ? 'POST' : 'GET', headers, ...(body ? { body: JSON.stringify(body) } : {}),
    cache: 'no-store', credentials: 'omit',
  });
  if (response.status === 204) return null;
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Request failed');
  return data;
}

async function ready() {
  password.value = '';
  document.querySelector('#new-password').value = '';
  change.hidden = true;
  const release = await request('/owner/releases');
  if (!release) { status.textContent = 'No Windows release is assigned to this account.'; return; }
  const url = new URL(release.download_url);
  if (url.protocol !== 'https:' || url.username || url.password) throw new Error('Invalid installer address');
  download.href = url.href;
  download.hidden = false;
  status.textContent = 'Your download link expires in two minutes.';
}

login.addEventListener('submit', async event => {
  event.preventDefault();
  download.hidden = true;
  status.textContent = 'Signing in…';
  try {
    const session = await request('/owner/sign-in', { login: document.querySelector('#login-id').value, password: password.value });
    token = session.token;
    login.hidden = true;
    if (session.must_change_password) {
      change.hidden = false;
      status.textContent = 'Set your own password to continue.';
      document.querySelector('#new-password').focus();
    } else await ready();
  } catch (error) { status.textContent = error.message; }
});
change.addEventListener('submit', async event => {
  event.preventDefault();
  try {
    const session = await request('/owner/password', { current_password: password.value, password: document.querySelector('#new-password').value });
    token = session.token;
    await ready();
  } catch (error) { status.textContent = error.message; }
});
