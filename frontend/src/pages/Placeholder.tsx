import { Card, Result, Tag, Typography } from 'antd'

export default function Placeholder({ phase, title, items }: { phase: number; title: string; items: string[] }) {
  return (
    <Card>
      <Result
        status="info"
        title={<>{title} <Tag color="blue">第 {phase} 阶段实现</Tag></>}
        subTitle="该模块尚未开发。页面不展示任何模拟数据。"
        extra={
          <div style={{ textAlign: 'left', display: 'inline-block' }}>
            <Typography.Text type="secondary">计划功能：</Typography.Text>
            <ul style={{ marginTop: 8 }}>
              {items.map((i) => <li key={i}>{i}</li>)}
            </ul>
          </div>
        }
      />
    </Card>
  )
}
