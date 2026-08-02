<script setup lang="ts">
import { computed } from 'vue'
import { brandAccent, brandSvgParts } from '@/utils/brandIcons'

const props = withDefaults(
  defineProps<{
    brand?: string | null
    size?: number
    /** Soft chip background using brand accent */
    filled?: boolean
  }>(),
  { size: 14, filled: false },
)

const parts = computed(() => brandSvgParts(props.brand))
const accent = computed(() => brandAccent(props.brand))
const multiColor = computed(() => parts.value.some((p) => Boolean(p.fill)))
</script>

<template>
  <span
    class="brand-mark"
    :class="{ filled, mono: !multiColor }"
    :style="{
      width: size + 'px',
      height: size + 'px',
      color: multiColor ? undefined : accent,
      background: filled ? (multiColor ? 'transparent' : accent) : 'transparent',
      boxShadow: filled && multiColor ? `0 0 0 1px color-mix(in srgb, ${accent} 35%, transparent)` : undefined,
    }"
    aria-hidden="true"
  >
    <svg
      :width="size"
      :height="size"
      viewBox="0 0 24 24"
      xmlns="http://www.w3.org/2000/svg"
      focusable="false"
    >
      <path
        v-for="(p, i) in parts"
        :key="i"
        :d="p.d"
        :fill="p.fill || 'currentColor'"
        :opacity="p.opacity ?? 1"
      />
    </svg>
  </span>
</template>

<style scoped>
.brand-mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  border-radius: 4px;
  line-height: 0;
  overflow: hidden;
}
.brand-mark.filled {
  border-radius: 5px;
  padding: 1px;
  box-sizing: content-box;
}
.brand-mark.filled.mono {
  /* monochrome glyph on brand-colored tile */
  color: #fff !important;
  padding: 2px;
}
.brand-mark svg {
  display: block;
}
</style>
