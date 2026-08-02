<script setup lang="ts">
import { computed } from 'vue'
import { serviceAccent, serviceSvgParts } from '@/utils/twofaServiceIcons'

const props = withDefaults(
  defineProps<{
    logo?: string | null
    issuer?: string | null
    size?: number
  }>(),
  { size: 40 },
)

const key = computed(() => props.logo || props.issuer || 'other')
const parts = computed(() => serviceSvgParts(key.value))
const accent = computed(() => serviceAccent(key.value))
/** Multi-color marks keep brand fills; mono marks paint white on accent tile */
const multi = computed(() => {
  const fills = parts.value.map((p) => p.fill).filter(Boolean)
  return fills.length > 1 || (fills.length === 1 && fills[0] !== accent.value)
})
const tileBg = computed(() =>
  multi.value ? 'var(--panel-soft, #f1f5f9)' : accent.value,
)
</script>

<template>
  <span
    class="svc-mark"
    :style="{
      width: size + 'px',
      height: size + 'px',
      background: tileBg,
      color: multi ? undefined : '#fff',
    }"
    aria-hidden="true"
  >
    <svg
      :width="Math.round(size * 0.62)"
      :height="Math.round(size * 0.62)"
      viewBox="0 0 24 24"
      xmlns="http://www.w3.org/2000/svg"
    >
      <path
        v-for="(p, i) in parts"
        :key="i"
        :d="p.d"
        :fill="multi ? p.fill || 'currentColor' : 'currentColor'"
        :opacity="p.opacity ?? 1"
      />
    </svg>
  </span>
</template>

<style scoped>
.svc-mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  border-radius: 12px;
  line-height: 0;
  overflow: hidden;
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--border, #e2e8f0) 80%, transparent);
}
.svc-mark svg {
  display: block;
}
</style>
