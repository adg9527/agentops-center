import { readFileSync } from 'node:fs'

const p = 'D:/sofrware/workbuddy/resources/wb-guest-sdk/wb.js'
const t = readFileSync(p, 'utf8')

const tokens = new Set()
const re = /(?:action|method|type|command|event)\s*[:=]\s*['"]([a-zA-Z][\w.\-]{2,40})['"]/g
let m
while ((m = re.exec(t))) tokens.add(m[1])

console.log('size', t.length)
console.log('has postMessage', t.includes('postMessage'))
console.log('has fetch(', t.includes('fetch('))
console.log('has WebSocket', t.includes('WebSocket'))
console.log('tokens:', [...tokens].slice(0, 80).join(', '))

// show a couple of representative API call sites
for (const key of ['invoke', 'request', 'call', 'send']) {
  const i = t.indexOf(key + '(')
  if (i > 0) console.log(`--- ${key} ctx ---\n` + t.slice(Math.max(0, i - 200), i + 200).replace(/\s+/g, ' '))
}
