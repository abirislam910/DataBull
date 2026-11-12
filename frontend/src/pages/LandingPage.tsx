import { ZodiacTaurus } from 'lucide-react'
import { Link } from 'react-router-dom'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'

export function LandingPage(): JSX.Element {
  return (
    <div className="flex min-h-screen items-center justify-center bg-bg px-6">
        <Card className="w-full max-w-md">
            <CardHeader>
            <span className="flex items-center gap-2 text-accent">
                <ZodiacTaurus className="h-5 w-5" aria-hidden />
                <span className="text-chrome font-medium">DataBull</span>
            </span>
            <CardTitle>Welcome to DataBull</CardTitle>
            <CardDescription>DataBull is a powerful data management platform designed to simplify your workflow.</CardDescription>
            </CardHeader>

            <CardContent>
                <p className="mt-6 text-center text-chrome text-text-secondary">
                    Get started by signing in or creating a new account.
                </p>
                <div className="mt-6 flex justify-center gap-4">
                    <Link to="/login">
                        <Button className="w-full">Sign In</Button>
                    </Link>
                    <Link to="/signup">
                        <Button variant="secondary" className="w-full">
                            Create Account
                        </Button>
                    </Link>
                </div>
            </CardContent>
        </Card>
    </div>
  )
}