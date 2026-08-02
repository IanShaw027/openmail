<script setup lang="ts">
withDefaults(
  defineProps<{
    size?: number | string
    title?: string
  }>(),
  {
    size: 28,
    title: 'OpenMail',
  },
)
</script>

<template>
  <svg
    xmlns="http://www.w3.org/2000/svg"
    :width="size"
    :height="size"
    viewBox="0 0 64 64"
    fill="none"
    role="img"
    :aria-label="title"
  >
    <rect width="64" height="64" rx="16" fill="#0A0A0A" />
    <path
      fill="#FAFAFA"
      d="M14 23.5c0-1.9 1.5-3.5 3.5-3.5h29c1.9 0 3.5 1.6 3.5 3.5v19c0 1.9-1.6 3.5-3.5 3.5h-29c-1.9 0-3.5-1.6-3.5-3.5z"
    />
    <path fill="#0A0A0A" d="M14 23.2 32 36.8 50 23.2V20c0-1.1-.9-2-2-2H16c-1.1 0-2 .9-2 2z" />
    <path
      d="M16.5 24.2 32 35.6 47.5 24.2"
      stroke="#FAFAFA"
      stroke-width="1.25"
      stroke-linecap="round"
      stroke-linejoin="round"
      opacity=".35"
    />
  </svg>
</template>
