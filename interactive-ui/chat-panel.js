const DEFAULT_SUGGESTIONS = [
  'Find software opportunities closing soon',
  'Which contracts fit an AI cloud vendor?',
  'Summarize the strongest cybersecurity matches'
];

const MOCK_SOURCES = [
  {
    title: 'Cloud modernization support services',
    agency: 'General Services Administration',
    deadline: '2026-07-18',
    url: 'https://sam.gov/opportunities',
    score: 0.84
  },
  {
    title: 'Cybersecurity operations assessment',
    agency: 'Virginia eVA',
    deadline: '2026-08-02',
    url: 'https://eva.virginia.gov/',
    score: 0.78
  }
];

function escapeHtml(value) {
  return String(value || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function normalizeSource(source) {
  const metadata = source.metadata || source;
  return {
    title: metadata.title || 'Untitled contract',
    agency: metadata.agency || metadata.organization || 'Agency not listed',
    deadline: metadata.deadline || 'Deadline not listed',
    url: metadata.url || '',
    score: source.score ?? metadata.score ?? null
  };
}

function renderSource(source) {
  const item = normalizeSource(source);
  const score = item.score === null ? '' : `<span>score ${Number(item.score).toFixed(3)}</span>`;
  const link = item.url
    ? `<a href="${escapeHtml(item.url)}" target="_blank" rel="noopener noreferrer">View</a>`
    : '';

  return `
    <div class="contract-chat__source">
      <div class="contract-chat__source-title">${escapeHtml(item.title)}</div>
      <div class="contract-chat__source-meta">
        <span>${escapeHtml(item.agency)}</span>
        <span>Due ${escapeHtml(item.deadline)}</span>
        ${score}
        ${link}
      </div>
    </div>`;
}

function renderMessage(message) {
  const sourceHtml = message.sources && message.sources.length
    ? `<div class="contract-chat__source-list">${message.sources.map(renderSource).join('')}</div>`
    : '';
  const metaHtml = message.indexSource
    ? `<div class="contract-chat__meta"><span>source: ${escapeHtml(message.indexSource)}</span></div>`
    : '';

  return `
    <div class="contract-chat__message contract-chat__message--${message.role}">
      <div class="contract-chat__bubble">${escapeHtml(message.content)}</div>
      ${metaHtml}
      ${sourceHtml}
    </div>`;
}

function mockAsk(question) {
  const lower = question.toLowerCase();
  const focus = lower.includes('cyber')
    ? 'cybersecurity and operations support'
    : lower.includes('cloud') || lower.includes('ai')
      ? 'AI, cloud, and modernization work'
      : 'the current opportunity set';

  return new Promise((resolve) => {
    window.setTimeout(() => {
      resolve({
        answer: `Based on the retrieved records, I would start with opportunities tied to ${focus}. Prioritize records with clear technical scope, near-term deadlines, and agencies that already describe software, data, security, or modernization requirements.`,
        sources: MOCK_SOURCES,
        index_source: 'mock-component'
      });
    }, 550);
  });
}

export class ContractChatPanel {
  constructor(root, options = {}) {
    this.root = root;
    this.options = {
      endpoint: 'http://localhost:5055/api/chat',
      mode: 'api',
      initialOpen: false,
      title: 'Contract Assistant',
      subtitle: 'Ask about contract opportunities',
      placeholder: 'Ask a question about contracts...',
      suggestions: DEFAULT_SUGGESTIONS,
      ...options
    };
    this.messages = [{
      role: 'assistant',
      content: 'Ask me to compare opportunities, explain match quality, or identify contracts for a specific vendor profile.',
      sources: [],
      indexSource: ''
    }];
    this.isOpen = Boolean(this.options.initialOpen);
    this.isLoading = false;
    this.error = '';
    this.render();
  }

  render() {
    const collapsedClass = this.isOpen ? '' : ' is-collapsed';
    const toggleIcon = this.isOpen ? 'ti-chevron-down' : 'ti-chevron-up';
    const toggleLabel = this.isOpen ? 'Collapse chat panel' : 'Expand chat panel';
    const suggestions = this.options.suggestions.map((item) => (
      `<button class="contract-chat__suggestion" type="button" data-suggestion="${escapeHtml(item)}">${escapeHtml(item)}</button>`
    )).join('');

    this.root.innerHTML = `
      <section class="contract-chat${collapsedClass}" aria-label="Contract chatbot">
        <div class="contract-chat__header">
          <div class="contract-chat__identity">
            <div class="contract-chat__icon" aria-hidden="true"><i class="ti ti-message-chatbot"></i></div>
            <div class="contract-chat__heading">
              <div class="contract-chat__title">${escapeHtml(this.options.title)}</div>
              <div class="contract-chat__subtitle">${escapeHtml(this.options.subtitle)}</div>
            </div>
          </div>
          <div class="contract-chat__actions">
            <button class="contract-chat__icon-btn" type="button" data-action="clear" title="Clear chat">
              <i class="ti ti-trash"></i>
            </button>
            <button class="contract-chat__icon-btn" type="button" data-action="toggle" title="${toggleLabel}" aria-expanded="${String(this.isOpen)}">
              <i class="ti ${toggleIcon}"></i>
            </button>
          </div>
        </div>

        <div class="contract-chat__body">
          <div class="contract-chat__messages" data-role="messages">
            ${this.messages.map(renderMessage).join('')}
            ${this.isLoading ? '<div class="contract-chat__typing" aria-label="Assistant is typing"><span></span><span></span><span></span></div>' : ''}
          </div>
          ${this.error ? `<div class="contract-chat__error">${escapeHtml(this.error)}</div>` : ''}
          <div class="contract-chat__suggestions">${suggestions}</div>
          <form class="contract-chat__composer" data-role="composer">
            <textarea class="contract-chat__input" rows="1" data-role="input" placeholder="${escapeHtml(this.options.placeholder)}"></textarea>
            <button class="contract-chat__send" type="submit" title="Send message" ${this.isLoading ? 'disabled' : ''}>
              <i class="ti ti-send-2"></i>
            </button>
          </form>
        </div>
      </section>`;

    this.bindEvents();
    this.scrollToBottom();
  }

  bindEvents() {
    this.root.querySelector('[data-action="toggle"]').addEventListener('click', () => {
      this.isOpen = !this.isOpen;
      this.render();
    });

    this.root.querySelector('[data-action="clear"]').addEventListener('click', () => {
      this.messages = [];
      this.error = '';
      this.render();
    });

    this.root.querySelectorAll('[data-suggestion]').forEach((button) => {
      button.addEventListener('click', () => {
        const input = this.root.querySelector('[data-role="input"]');
        input.value = button.dataset.suggestion || '';
        input.focus();
      });
    });

    const form = this.root.querySelector('[data-role="composer"]');
    const input = this.root.querySelector('[data-role="input"]');
    form.addEventListener('submit', (event) => {
      event.preventDefault();
      this.submit(input.value);
    });
    input.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        this.submit(input.value);
      }
    });
    input.addEventListener('input', () => {
      input.style.height = 'auto';
      input.style.height = `${Math.min(input.scrollHeight, 112)}px`;
    });
  }

  async submit(rawQuestion) {
    const question = rawQuestion.trim();
    if (!question || this.isLoading) return;

    this.messages.push({ role: 'user', content: question, sources: [], indexSource: '' });
    this.isLoading = true;
    this.error = '';
    this.render();

    try {
      const response = await this.ask(question);
      this.messages.push({
        role: 'assistant',
        content: response.answer || 'No answer returned.',
        sources: response.sources || [],
        indexSource: response.index_source || response.indexSource || ''
      });
    } catch (error) {
      this.error = error.message || 'Chat request failed.';
    } finally {
      this.isLoading = false;
      this.render();
    }
  }

  async ask(question) {
    if (this.options.mode === 'mock') {
      return mockAsk(question);
    }

    const response = await fetch(this.options.endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question })
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(data.error || `HTTP ${response.status}`);
    }
    return data;
  }

  scrollToBottom() {
    const messages = this.root.querySelector('[data-role="messages"]');
    if (messages) {
      messages.scrollTop = messages.scrollHeight;
    }
  }

  static mount(target, options = {}) {
    const root = typeof target === 'string' ? document.querySelector(target) : target;
    if (!root) {
      throw new Error('ContractChatPanel target not found.');
    }
    return new ContractChatPanel(root, options);
  }
}

export function mountContractChatPanel(target, options = {}) {
  return ContractChatPanel.mount(target, options);
}
