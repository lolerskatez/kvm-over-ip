/* Theme and UI Management */

class ThemeManager {
  constructor() {
    this.theme = localStorage.getItem('theme') || 'light';
    this.init();
  }
  
  init() {
    this.applyTheme(this.theme);
    this.setupListeners();
  }
  
  applyTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('theme', theme);
    this.theme = theme;
    this.updateThemeToggleButton();
  }
  
  toggle() {
    const newTheme = this.theme === 'light' ? 'dark' : 'light';
    this.applyTheme(newTheme);
  }
  
  setupListeners() {
    const toggleBtn = document.querySelector('.theme-toggle');
    if (toggleBtn) {
      toggleBtn.addEventListener('click', () => this.toggle());
    }
  }
  
  updateThemeToggleButton() {
    const toggleBtn = document.querySelector('.theme-toggle');
    if (toggleBtn) {
      const iconClass = this.theme === 'light' ? 'icon-moon' : 'icon-sun';
      toggleBtn.innerHTML = '<span class="icon icon-md ' + iconClass + '"></span>';
      toggleBtn.title = this.theme === 'light' ? 'Switch to dark mode' : 'Switch to light mode';
    }
  }
}

/* Navigation Toggle */
class Navigation {
  constructor() {
    this.init();
  }
  
  init() {
    const toggleBtn = document.querySelector('.nav-toggle');
    const sidebar = document.querySelector('.sidebar');
    
    if (toggleBtn && sidebar) {
      toggleBtn.addEventListener('click', () => {
        sidebar.classList.toggle('show');
      });
      
      // Close sidebar when clicking on a link (mobile)
      const links = sidebar.querySelectorAll('a');
      links.forEach(link => {
        link.addEventListener('click', () => {
          if (window.innerWidth <= 768) {
            sidebar.classList.remove('show');
          }
        });
      });
    }
  }
}

/* Dropdown Menu Management */
class DropdownManager {
  constructor() {
    this.init();
  }
  
  init() {
    const dropdowns = document.querySelectorAll('.user-profile');
    
    dropdowns.forEach(dropdown => {
      dropdown.addEventListener('click', (e) => {
        e.preventDefault();
        const menu = dropdown.nextElementSibling;
        if (menu && menu.classList.contains('dropdown-menu')) {
          menu.classList.toggle('show');
        }
      });
    });
    
    // Close dropdowns when clicking outside
    document.addEventListener('click', (e) => {
      if (!e.target.closest('.user-menu')) {
        document.querySelectorAll('.dropdown-menu.show').forEach(menu => {
          menu.classList.remove('show');
        });
      }
    });
  }
}

/* Active Link Indicator */
class ActiveLink {
  constructor() {
    this.init();
  }
  
  init() {
    const currentPath = window.location.pathname;
    const links = document.querySelectorAll('.sidebar-link, .header-nav a');
    
    links.forEach(link => {
      const href = link.getAttribute('href');
      if (href && currentPath.includes(href)) {
        link.classList.add('active');
      }
    });
  }
}

/* Modal Management */
class ModalManager {
  constructor() {
    this.init();
  }
  
  init() {
    // Close modal when clicking close button
    document.querySelectorAll('.modal-close').forEach(btn => {
      btn.addEventListener('click', (e) => {
        const modal = e.target.closest('.modal');
        if (modal) {
          this.closeModal(modal);
        }
      });
    });
    
    // Close modal when clicking outside content
    document.querySelectorAll('.modal').forEach(modal => {
      modal.addEventListener('click', (e) => {
        if (e.target === modal) {
          this.closeModal(modal);
        }
      });
    });
  }
  
  openModal(modalId) {
    const modal = document.getElementById(modalId);
    if (modal) {
      modal.classList.add('show');
      document.body.style.overflow = 'hidden';
    }
  }
  
  closeModal(modal) {
    modal.classList.remove('show');
    document.body.style.overflow = 'auto';
  }
}

/* Form Validation */
class FormValidator {
  static validate(form) {
    const inputs = form.querySelectorAll('input, textarea, select');
    let isValid = true;
    
    inputs.forEach(input => {
      const error = this.validateField(input);
      this.showError(input, error);
      if (error) isValid = false;
    });
    
    return isValid;
  }
  
  static validateField(field) {
    const value = field.value.trim();
    const required = field.hasAttribute('required');
    const type = field.type;
    
    if (required && !value) {
      return `${field.name || 'This field'} is required`;
    }
    
    if (type === 'email' && value) {
      const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
      if (!emailRegex.test(value)) {
        return 'Please enter a valid email address';
      }
    }
    
    if (type === 'password' && value) {
      if (value.length < 8) {
        return 'Password must be at least 8 characters';
      }
    }
    
    return null;
  }
  
  static showError(field, error) {
    const errorEl = field.parentElement.querySelector('.form-error');
    
    if (error) {
      field.setAttribute('aria-invalid', 'true');
      if (errorEl) {
        errorEl.textContent = error;
      } else {
        const newError = document.createElement('div');
        newError.className = 'form-error';
        newError.textContent = error;
        field.parentElement.appendChild(newError);
      }
    } else {
      field.removeAttribute('aria-invalid');
      if (errorEl) {
        errorEl.remove();
      }
    }
  }
}

/* Loading State Management */
class LoadingManager {
  static show(element = document.body) {
    element.style.opacity = '0.6';
    element.style.pointerEvents = 'none';
  }
  
  static hide(element = document.body) {
    element.style.opacity = '1';
    element.style.pointerEvents = 'auto';
  }
  
  static showSpinner(container) {
    const spinner = document.createElement('div');
    spinner.className = 'spinner';
    spinner.id = 'loader-spinner';
    container.appendChild(spinner);
  }
  
  static hideSpinner() {
    const spinner = document.getElementById('loader-spinner');
    if (spinner) spinner.remove();
  }
}

/* Toast Notifications */
class Toast {
  static show(message, type = 'info', duration = 3000) {
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.textContent = message;
    toast.style.cssText = `
      position: fixed;
      bottom: 20px;
      right: 20px;
      background-color: var(--color-${type === 'error' ? 'danger' : type});
      color: white;
      padding: 16px 20px;
      border-radius: var(--radius-md);
      box-shadow: 0 4px 12px rgba(0, 0, 0, 0.2);
      z-index: 9999;
      animation: slideIn 0.3s ease-out;
    `;
    
    document.body.appendChild(toast);
    
    setTimeout(() => {
      toast.style.animation = 'slideOut 0.3s ease-out';
      setTimeout(() => toast.remove(), 300);
    }, duration);
  }
}

/* Confirmation Dialog */
class Confirm {
  static show(message, onConfirm, onCancel = null) {
    const modal = document.createElement('div');
    modal.className = 'modal show';
    modal.innerHTML = `
      <div class="modal-content">
        <div class="modal-header">
          <h2>Confirm Action</h2>
          <button class="modal-close">×</button>
        </div>
        <div class="modal-body">
          <p>${message}</p>
        </div>
        <div class="modal-footer">
          <button class="btn btn-secondary cancel-btn">Cancel</button>
          <button class="btn btn-danger confirm-btn">Confirm</button>
        </div>
      </div>
    `;
    
    document.body.appendChild(modal);
    
    const confirmBtn = modal.querySelector('.confirm-btn');
    const cancelBtn = modal.querySelector('.cancel-btn');
    const closeBtn = modal.querySelector('.modal-close');
    
    const cleanup = () => modal.remove();
    
    confirmBtn.addEventListener('click', () => {
      onConfirm();
      cleanup();
    });
    
    cancelBtn.addEventListener('click', cleanup);
    closeBtn.addEventListener('click', cleanup);
    
    modal.addEventListener('click', (e) => {
      if (e.target === modal) cleanup();
    });
  }
}

/* Add animation styles */
const style = document.createElement('style');
style.textContent = `
  @keyframes slideIn {
    from {
      transform: translateX(400px);
      opacity: 0;
    }
    to {
      transform: translateX(0);
      opacity: 1;
    }
  }
  
  @keyframes slideOut {
    from {
      transform: translateX(0);
      opacity: 1;
    }
    to {
      transform: translateX(400px);
      opacity: 0;
    }
  }
`;
document.head.appendChild(style);

/* Initialize on DOM Ready */
document.addEventListener('DOMContentLoaded', () => {
  new ThemeManager();
  new Navigation();
  new DropdownManager();
  new ActiveLink();
  new ModalManager();
  
  // Add form submission handler
  document.querySelectorAll('form').forEach(form => {
    if (form.getAttribute('data-validate') !== 'false') {
      form.addEventListener('submit', (e) => {
        if (!FormValidator.validate(form)) {
          e.preventDefault();
        }
      });
    }
  });
});
