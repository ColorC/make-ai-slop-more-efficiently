import { describe, expect, it } from 'vitest'
import { relTimeEn, relTimeZh } from './time'

describe('relTimeZh', () => {
  it('空值与非法输入返回空串', () => {
    expect(relTimeZh(null)).toBe('')
    expect(relTimeZh(undefined)).toBe('')
    expect(relTimeZh('not-a-date')).toBe('')
  })

  it('一小时内按分钟、当天按小时、更久按天', () => {
    const ago = (sec: number) => new Date(Date.now() - sec * 1000).toISOString()
    expect(relTimeZh(ago(5 * 60))).toBe('5分钟前')
    expect(relTimeZh(ago(3 * 3600))).toBe('3小时前')
    expect(relTimeZh(ago(2 * 86400))).toBe('2天前')
  })

  it('未来时间钳到 0', () => {
    const future = new Date(Date.now() + 60_000).toISOString()
    expect(relTimeZh(future)).toBe('0分钟前')
  })
})

describe('relTimeEn', () => {
  it('0/空返回空串', () => {
    expect(relTimeEn(0)).toBe('')
    expect(relTimeEn(null)).toBe('')
    expect(relTimeEn(undefined)).toBe('')
  })

  it('秒/分/时/天/月分档', () => {
    const ago = (sec: number) => Date.now() / 1000 - sec
    expect(relTimeEn(ago(30))).toBe('30s')
    expect(relTimeEn(ago(10 * 60))).toBe('10m')
    expect(relTimeEn(ago(5 * 3600))).toBe('5h')
    expect(relTimeEn(ago(3 * 86400))).toBe('3d')
    expect(relTimeEn(ago(70 * 86400))).toBe('2mo')
  })
})
