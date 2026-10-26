<script setup>
import { inject } from 'vue'
const target = inject('settings-header-actions-target', null)
</script>
<template>
  <Teleport v-if="target" defer :to="target"><div class="settings-save-actions"><slot /></div></Teleport>
  <div v-else class="settings-save-actions"><slot /></div>
</template>
