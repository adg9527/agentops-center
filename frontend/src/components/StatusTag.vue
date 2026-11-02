<script setup lang="ts">
import { computed } from 'vue'
import { taskStatus } from '../labels'

const props = defineProps<{ status: string }>()
const s = computed(() => taskStatus(props.status))
</script>

<template>
  <a-tag :color="s.color">{{ s.text }}</a-tag>
</template>
