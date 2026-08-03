import { useNavigate } from 'react-router-dom';
import { DataStateEmpty } from './DataState';

interface ConnectAccountEmptyProps {
    /** What this particular page will show once an account is connected. */
    description: string;
}

/**
 * Stands in for a page's content when no Chess.com username is set.
 *
 * These pages used to redirect to Home. A silent bounce makes the sidebar link
 * look broken: nothing is announced, and to a screen-reader user the page
 * simply never changes. Explaining in place keeps the user oriented and names
 * the one action that fixes it.
 *
 * The action navigates to Home rather than opening the username editor: that
 * editor lives inside `UsernameDisplay`, which `Layout` only mounts once a
 * username exists, so there is nothing to open in exactly this state. Home's
 * onboarding form is the only working way in.
 */
export function ConnectAccountEmpty({ description }: ConnectAccountEmptyProps) {
    const navigate = useNavigate();

    return (
        <DataStateEmpty
            title="Connect your Chess.com account"
            description={description}
            actionLabel="Connect account"
            onAction={() => navigate('/')}
        />
    );
}
