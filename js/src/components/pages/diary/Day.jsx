import React from 'react';
import {Grid, Typography} from "@material-ui/core";
import {
    ClimbField,
    EditField,
    FloatField,
    HRZoneField,
    IntegerField,
    JupyterActivity,
    NearbyMenu,
    ScoreField,
    ShrimpField,
    TextField,
    ValueField
} from "./elements";
import {makeStyles} from "@material-ui/core/styles";
import {ColumnCard, ColumnList, LinkButton, Loading, Text, Thumbnail} from "../../elements";
import {setIds} from "../../functions";


const useStyles = makeStyles(theme => ({
    grid: {
        justifyContent: 'flex-start',
        alignItems: 'baseline',
    },
    center: {
        textAlign: 'center',
    },
    img: {
        marginBottom: '-5px',
    },
}));


export default function Day(props) {

    const {writer, json, history} = props;

    if (json === null) {
        return <Loading/>;  // undefined initial data
    } else {
        setIds(json);
        // drop outer date label since we already have that in the page
        return (<ColumnList>
            {json.slice(1).map(row => <TopLevelPaper writer={writer} json={row} history={history} key={row.id}/>)}
        </ColumnList>);
    }
}


function childrenFromRest(head, rest, writer, level, history) {

    const classes = useStyles();
    let children = [];

    rest.forEach((row, index) => {
        if (Array.isArray(row)) {
            if (head.tag === 'shrimp') {
                children.push(<ShrimpField json={row} key={row.id}/>);
            } else if (head.tag === 'hr-zones-time') {
                children.push(<HRZoneField json={row} key={row.id}/>);
            } else {
                children.push(<Header writer={writer} json={row} level={level} history={history} key={row.id}/>);
            }
        } else {
            if (head.tag === 'activity' && index === 0 && row.label === 'Name' && row.type === 'edit') {
                children.push(<EditField writer={writer} json={row} xs={10}/>);
                children.push(<Grid item xs={2}><Thumbnail activity_id={head.db} className={classes.img}/></Grid>);
            } else {
                children.push(<Field writer={writer} json={row} key={row.id}/>);
            }
        }
    });
    return children;
}


function TopLevelPaper(props) {

    const {writer, json, history} = props;
    const [head, ...rest] = json;
    const children = childrenFromRest(head, rest, writer, 3, history);

    return (<ColumnCard header={head.value}>{children}</ColumnCard>);
}


function Header(props) {

    const {writer, json, level, history} = props;
    const [head, ...rest] = json;
    const classes = useStyles();

    const children = head.tag === 'jupyter-activity' ?
        <JupyterActivity json={rest}/> :
        childrenFromRest(head, rest, writer, level + 1, history);

    if (head.tag === 'climb') {
        return (<ClimbField json={json}/>);
    } else if (head.tag === 'nearby-links') {
        return (<NearbyMenu json={json} history={history}/>);
    } else {
        return (<>
            <Grid item xs={12} className={classes.grid}>
                <Typography variant={'h' + level}>{head.value}</Typography>
            </Grid>
            {children}
        </>);
    }
}


function Field(props) {

    const {writer, json} = props;
    const classes = useStyles();

    if (json.type === 'edit') {
        return <EditField writer={writer} json={json}/>
    } else if (json.type === 'integer') {
        return <IntegerField writer={writer} json={json}/>
    } else if (json.type === 'float') {
        return <FloatField writer={writer} json={json}/>
    } else if (json.type === 'score') {
        return <ScoreField writer={writer} json={json}/>
    } else if (json.type === 'text') {
        return <TextField json={json}/>
    } else if (json.type === 'value') {
        return <ValueField json={json}/>
    } else if (json.type === 'link') {
        if (json.tag === 'health') {
            return (<Grid item xs={4} className={classes.center}>
                <LinkButton href='api/jupyter/health'><Text>{json.value}</Text></LinkButton>
            </Grid>);
        } else {
            return (<Grid item xs={4}>
                <Text>Unsupported link: {JSON.stringify(json)}</Text>
            </Grid>);
        }
    } else {
        return (<Grid item xs={4}>
            <Text>Unsupported type: {JSON.stringify(json)}</Text>
        </Grid>);
    }
}
