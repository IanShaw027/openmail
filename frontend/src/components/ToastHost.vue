<script setup lang="ts">
import { useToastList, type ToastKind } from '@/composables/useToast'

const { toasts, dismissToast } = useToastList()

function kindClass(kind: ToastKind) {
  if (kind === 'danger') return 'toast--danger'
  if (kind === 'info') return 'toast--info'
  return 'toast--success'
}
</script>

<template>
  <div class="toast-host" aria-live="polite" aria-relevant="additions">
    <TransitionGroup name="toast">
      <div
        v-for="item in toasts"
        :key="item.id"
        class="toast"
        :class="kindClass(item.kind)"
        role="status"
      >
        <span class="toast-msg">{{ item.msg }}</span>
        <button
          type="button"
          class="toast-close"
          :aria-label="'Close'"
          @click="dismissToast(item.id)"
        >
          ×
        </button>
      </div>
    </TransitionGroup>
  </div>
</template>

<style scoped>
.toast-host {
  position: fixed;
  top: calc(var(--nav-h, 56px) + 12px);
  right: 16px;
  z-index: var(--z-toast, 200);
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 8px;
  max-width: min(420px, calc(100vw - 24px));
  pointer-events: none;
}

.toast {
  pointer-events: auto;
  display: flex;
  align-items: flex-start;
  gap: 10px;
  min-width: min(280px, 100%);
  max-width: 100%;
  padding: 12px 14px;
  border-radius: var(--control-radius, 12px);
  font-size: var(--control-font, 13px);
  line-height: 1.45;
  box-shadow: var(--shadow-lg);
  border: 1px solid transparent;
  backdrop-filter: blur(10px);
}

.toast--success {
  background: color-mix(in srgb, var(--success-soft, #dcfce7) 92%, #fff);
  color: var(--success, #15803d);
  border-color: color-mix(in srgb, var(--success, #15803d) 22%, transparent);
}

.toast--danger {
  background: color-mix(in srgb, var(--danger-soft, #fee2e2) 92%, #fff);
  color: var(--danger, #b91c1c);
  border-color: color-mix(in srgb, var(--danger, #b91c1c) 22%, transparent);
}

.toast--info {
  background: color-mix(in srgb, var(--accent-soft, #e0e7ff) 92%, #fff);
  color: var(--accent, #3730a3);
  border-color: color-mix(in srgb, var(--accent, #3730a3) 22%, transparent);
}

.toast-msg {
  flex: 1 1 auto;
  word-break: break-word;
}

.toast-close {
  flex-shrink: 0;
  width: 22px;
  height: 22px;
  margin: -2px -4px 0 0;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: inherit;
  opacity: 0.55;
  font-size: 16px;
  line-height: 1;
  cursor: pointer;
}

.toast-close:hover {
  opacity: 1;
  background: rgba(0, 0, 0, 0.06);
}

.toast-enter-active,
.toast-leave-active {
  transition:
    opacity 0.2s ease,
    transform 0.2s ease;
}
.toast-enter-from {
  opacity: 0;
  transform: translateY(-8px) scale(0.98);
}
.toast-leave-to {
  opacity: 0;
  transform: translateX(12px);
}
.toast-move {
  transition: transform 0.2s ease;
}

@media (max-width: 480px) {
  .toast-host {
    left: 12px;
    right: 12px;
    align-items: stretch;
  }
  .toast {
    min-width: 0;
  }
}
</style>
