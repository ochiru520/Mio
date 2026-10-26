<script>
import { defineComponent, h, ref, onMounted } from 'vue'
import AppSelect from './AppSelect.vue'

function text(children) {
  return Array.isArray(children) ? children.map(child => typeof child === 'object' ? text(child.children) : String(child ?? '')).join('') : String(children ?? '')
}
function optionsFrom(nodes, group = '') {
  return (nodes || []).flatMap(node => {
    if (!node || typeof node !== 'object') return []
    if (node.type === 'option') return [{ value: node.props?.value ?? text(node.children),
      label: `${group}${text(node.children)}`, disabled: node.props?.disabled === '' || node.props?.disabled === true }]
    const prefix = node.type === 'optgroup' ? `${node.props?.label || ''} · ` : group
    return Array.isArray(node.children) ? optionsFrom(node.children, prefix) : []
  })
}

export default defineComponent({
  name: 'SelectField', inheritAttrs: false,
  props: { modelValue: { default: undefined }, value: { default: undefined }, modelModifiers: { default: () => ({}) }, disabled: Boolean },
  emits: ['update:modelValue', 'change'],
  setup(props, { attrs, slots, emit }) {
    const native = ref(null), label = ref('选择选项'), invalid = ref(false)
    onMounted(() => {
      const parent = native.value?.closest('label')
      label.value = parent?.querySelector('strong, span')?.textContent?.trim() || parent?.firstChild?.textContent?.trim() || label.value
    })
    function choose(value) {
      let selected = value
      if (props.modelModifiers.number && typeof selected === 'string' && selected.trim() && Number.isFinite(Number(selected))) selected = Number(selected)
      emit('update:modelValue', selected)
      invalid.value = false
      native.value.value = String(value)
      native.value.dispatchEvent(new Event('change', { bubbles: true }))
    }
    return () => {
      const nodes = slots.default?.() || []
      const selected = props.modelValue !== undefined ? props.modelValue : props.value
      return h('span', { class: ['select-field', attrs.class], style: attrs.style }, [
        h('select', { ref: native, value: selected, disabled: props.disabled, required: attrs.required, name: attrs.name, tabindex: -1, 'aria-hidden': true,
          style: { display: 'none' }, onChange: event => emit('change', event),
          onInvalid: event => {
            event.preventDefault()
            invalid.value = true
            native.value?.parentElement?.querySelector('[role="combobox"]')?.focus()
          } }, nodes),
        h(AppSelect, { ...Object.fromEntries(Object.entries(attrs).filter(([key]) => !['class', 'style', 'aria-label', 'required', 'name'].includes(key))),
          modelValue: selected, options: optionsFrom(nodes), disabled: props.disabled,
          label: attrs['aria-label'] || label.value, 'aria-required': attrs.required !== undefined && attrs.required !== false,
          'aria-invalid': invalid.value || undefined, 'onUpdate:modelValue': choose }),
        invalid.value ? h('small', { role: 'alert' }, '请选择一个选项。') : null,
      ])
    }
  },
})
</script>
<style scoped>
.select-field { display: block; min-width: 0; width: 100%; }
/* Page action-button rules must not shrink a select trigger to an icon button. */
.select-field > :deep(button.app-select) { display: flex; width: 100%; height: auto; min-height: 36px; flex: 1 1 auto; justify-content: space-between; gap: 10px; padding: 8px 10px; }
</style>
