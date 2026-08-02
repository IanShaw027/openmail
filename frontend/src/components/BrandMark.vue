<script setup lang="ts">
import { computed } from 'vue'
import { brandAccent, brandSvgPath } from '@/utils/brandIcons'

const props = withDefaults(
  defineProps<{
    brand?: string | null
    size?: number
    /** filled chip style uses white glyph on brand color */
    filled?: boolean
  }>(),
  { size: 14, filled: false },
)

const path = computed(() => brandSvgPath(props.brand))
const color = computed(() => brandAccent(props.brand))
</script>

<template>
  <span
    class="brand-mark"
    :class="{ filled }"
    :style="{
      width: size + 'px',
      height: size + 'px',
      color: filled ? '#fff' : color,
      background: filled ? color : 'transparent',
    }"
    aria-hidden="true"
  >
    <svg :width="size" :height="size" viewBox="0 0 24 24" fill="currentColor">
      <path :d="path" />
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
}
.brand-mark.filled {
  border-radius: 5px;
  padding: 2px;
  box-sizing: content-box;
}
.brand-mark svg {
  display: block;
}
</style>
