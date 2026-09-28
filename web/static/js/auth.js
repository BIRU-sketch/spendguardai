const tabs = document.querySelectorAll('.auth-tab');
const forms = document.querySelectorAll('.auth-form');
const switchToSignup = document.getElementById('switch-to-signup');
const switchToSignin = document.getElementById('switch-to-signin');

function switchTab(tabName) {
  tabs.forEach(tab => {
    tab.classList.toggle('active', tab.dataset.tab === tabName);
  });

  forms.forEach(form => {
    form.classList.toggle('active', form.id === `${tabName}-form`);
  });
}

tabs.forEach(tab => {
  tab.addEventListener('click', () => switchTab(tab.dataset.tab));
});

switchToSignup.addEventListener('click', event => {
  event.preventDefault();
  switchTab('signup');
});

switchToSignin.addEventListener('click', event => {
  event.preventDefault();
  switchTab('signin');
});

function validateEmail(email) {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
}

function validateForm(formType) {
  let isValid = true;

  if (formType === 'signin') {
    const email = document.getElementById('signin-email');
    const password = document.getElementById('signin-password');
    const emailError = document.getElementById('signin-email-error');
    const passwordError = document.getElementById('signin-password-error');

    [email, password].forEach(element => element.classList.remove('error'));
    [emailError, passwordError].forEach(error => error.classList.remove('visible'));

    if (!validateEmail(email.value)) {
      email.classList.add('error');
      emailError.classList.add('visible');
      isValid = false;
    }

    if (password.value.length < 1) {
      password.classList.add('error');
      passwordError.classList.add('visible');
      isValid = false;
    }
  } else {
    const name = document.getElementById('signup-name');
    const email = document.getElementById('signup-email');
    const password = document.getElementById('signup-password');
    const confirmPassword = document.getElementById('signup-confirm-password');
    const nameError = document.getElementById('signup-name-error');
    const emailError = document.getElementById('signup-email-error');
    const passwordError = document.getElementById('signup-password-error');
    const confirmPasswordError = document.getElementById('signup-confirm-password-error');

    [name, email, password, confirmPassword].forEach(element => element.classList.remove('error'));
    [nameError, emailError, passwordError, confirmPasswordError].forEach(error => error.classList.remove('visible'));

    if (name.value.trim().length < 1) {
      name.classList.add('error');
      nameError.classList.add('visible');
      isValid = false;
    }

    if (!validateEmail(email.value)) {
      email.classList.add('error');
      emailError.classList.add('visible');
      isValid = false;
    }

    if (password.value.length < 8) {
      password.classList.add('error');
      passwordError.classList.add('visible');
      isValid = false;
    }

    if (password.value !== confirmPassword.value) {
      confirmPassword.classList.add('error');
      confirmPasswordError.classList.add('visible');
      isValid = false;
    }
  }

  return isValid;
}

document.getElementById('signin-form').addEventListener('submit', event => {
  if (!validateForm('signin')) {
    event.preventDefault();
  }
});

document.getElementById('signup-form').addEventListener('submit', async event => {
  event.preventDefault();

  if (!validateForm('signup')) {
    return;
  }

  const form = event.currentTarget;
  const submitButton = form.querySelector('.btn-submit');
  const successMessage = document.getElementById('success-message');
  const payload = {
    name: document.getElementById('signup-name').value.trim(),
    email: document.getElementById('signup-email').value.trim(),
    password: document.getElementById('signup-password').value,
    confirm_password: document.getElementById('signup-confirm-password').value
  };

  submitButton.disabled = true;

  try {
    const response = await fetch(form.action, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const result = await response.json();

    if (!response.ok) {
      throw new Error(result.error || 'Signup failed. Please try again.');
    }

    if (result.status === 'pending_confirmation') {
      successMessage.textContent = result.message;
      successMessage.classList.add('visible');
      return;
    }

    successMessage.textContent = 'Account created successfully! Redirecting...';
    successMessage.classList.add('visible');
    setTimeout(() => { window.location.href = '/dashboard'; }, 2000);
  } catch (error) {
    successMessage.textContent = error.message;
    successMessage.classList.add('visible');
  } finally {
    submitButton.disabled = false;
  }
});

const urlParams = new URLSearchParams(window.location.search);
if (urlParams.get('success') === 'true') {
  const successMessage = document.getElementById('success-message');
  successMessage.classList.add('visible');
  setTimeout(() => { window.location.href = '/'; }, 2000);
}
