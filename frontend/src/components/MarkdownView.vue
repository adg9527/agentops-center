<script setup lang="ts">
import { computed } from 'vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'

const props = defineProps<{ content: string }>()

const html = computed(() => {
  const raw = marked.parse(props.content ?? '', { async: false, breaks: true, gfm: true }) as string
  return DOMPurify.sanitize(raw, { ADD_ATTR: ['target'] })
})
</script>

<template>
  <div class="md" v-html="html" />
</template>

<style scoped>
.md {
  font-size: 13px;
  line-height: 1.6;
  word-break: break-word;
}

.md :deep(h1),
.md :deep(h2),
.md :deep(h3),
.md :deep(h4) {
  margin: 8px 0 4px;
  font-weight: 600;
  line-height: 1.3;
}

.md :deep(h1) {
  font-size: 18px;
}

.md :deep(h2) {
  font-size: 16px;
}

.md :deep(h3) {
  font-size: 14px;
}

.md :deep(p) {
  margin: 4px 0;
}

.md :deep(ul),
.md :deep(ol) {
  margin: 4px 0;
  padding-left: 20px;
}

.md :deep(li) {
  margin: 2px 0;
}

.md :deep(code) {
  background: rgba(0, 0, 0, 0.06);
  padding: 1px 4px;
  border-radius: 4px;
  font-family: Consolas, 'Cascadia Code', monospace;
  font-size: 12px;
}

.md :deep(pre) {
  background: #f6f8fa;
  padding: 8px 10px;
  border-radius: 6px;
  overflow: auto;
  max-height: 320px;
  margin: 6px 0;
}

.md :deep(pre code) {
  background: none;
  padding: 0;
}

.md :deep(table) {
  border-collapse: collapse;
  margin: 6px 0;
  font-size: 12px;
}

.md :deep(th),
.md :deep(td) {
  border: 1px solid #e5e7eb;
  padding: 4px 8px;
}

.md :deep(th) {
  background: #fafafa;
}

.md :deep(blockquote) {
  margin: 6px 0;
  padding-left: 10px;
  border-left: 3px solid #d9d9d9;
  color: #666;
}

.md :deep(a) {
  color: #1677ff;
}

.md :deep(img) {
  max-width: 100%;
}

.md :deep(hr) {
  border: none;
  border-top: 1px solid #eee;
  margin: 8px 0;
}
</style>
